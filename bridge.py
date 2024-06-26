"""
 ________________________________________
< Maubot bridge bot for Talks Hippy bots >
 ----------------------------------------
        \   ^__^
         \  (oo)\_______
            (__)\       )\/\
                ||----w |
                ||     ||
"""

import asyncio
import base64
import functools
import re
from collections import defaultdict, deque
from enum import Enum
from io import BytesIO
from threading import RLock
from typing import Type, Optional

import cachetools
import jsonpickle
import requests
from config import Config
from maubot import Plugin, MessageEvent
from maubot.handlers import event
from maubot.matrix import parse_formatted
from mautrix.types import EventType, TextMessageEventContent, MessageType, Format, LocationMessageEventContent, \
    MediaMessageEventContent, ContentURI, ImageInfo, AudioInfo, VideoInfo, FileInfo, Event, RedactionEvent
from mautrix.util.config import BaseProxyConfig
from requests.adapters import HTTPAdapter
from urllib3.exceptions import NewConnectionError

try:
    import magic
except ImportError:
    magic = None

try:
    from PIL import Image
except ImportError:
    Image = None


class TalksReceiveMessageRequest:
    def __init__(self, timestamp, room_id, event_id, sender_id, event_type, body, message_type,
                 body_format, formatted_body, geo_uri, mime_type, mxc_uri, encoded_bytes: bytes):
        self.timestamp = timestamp
        self.roomId = room_id
        self.eventId = event_id
        self.senderId = sender_id
        self.eventType = event_type
        self.body = body
        self.messageType = f"{message_type}"
        self.format = body_format
        self.formattedBody = formatted_body
        self.geoUri = geo_uri
        self.mimeType = mime_type
        self.mxcUri = mxc_uri
        self.bytes = encoded_bytes.decode('utf-8') if encoded_bytes else None


class TalksConfirmMessageRequest:

    class Message:
        def __init__(self, source_id, matrix_id, mxc_uri):
            self.sourceId = source_id
            self.matrixId = matrix_id
            self.mxcUri = mxc_uri

    def __init__(self, messages):
        self.messages = messages


class TalksTagRoomRequest:
    def __init__(self, room_id, tag, value):
        self.roomId = room_id
        self.tag = tag
        self.value = value


class MediaCache:
    mxc_uri: ContentURI
    file_name: str
    mime_type: str
    width: int
    height: int
    size: int

    def __init__(self, mxc_uri: ContentURI, file_name: str, mime_type: str,
                 width: int, height: int, duration: int, size: int) -> None:
        self.mxc_uri = mxc_uri
        self.file_name = file_name
        self.mime_type = mime_type
        self.width = width
        self.height = height
        self.duration = duration
        self.size = size


class TalksResponse:

    class Message:
        def __init__(self, room_id, message_type, body_type, body, talks_id):
            self.roomId = room_id
            self.messageType = message_type
            self.bodyType = body_type
            self.body = body
            self.id = talks_id

    def __init__(self, description, messages):
        self.description = description
        self.messages = messages


class BridgeException(Exception):
    def __init__(self, message):
        self.message = message


class BridgeBot(Plugin):
    """
     _________________________________________
    / The bridge main class, which listens to \
    | matrix events and also dispatches a     |
    | task to poll the Talks Hippy bot for    |
    \ messages                                /
     -----------------------------------------
            \   ^__^
             \  (oo)\_______
                (__)\       )\/\
                    ||----w |
                    ||     ||
    """

    MATRIX_BOT_USER = None
    USER_ID_SKIP_LIST = None

    TALKS_BASE_URL = None
    TALKS_API_KEY = None
    TALKS_RECEIVE_MESSAGE = None
    TALKS_RECEIVE_MESSAGE_TIMEOUT= None
    TALKS_GET_MESSAGES = None
    TALKS_CONFIRM_MESSAGES = None
    TALKS_TAG_ROOM = None

    BOT_ON_REGEX = None
    BOT_OFF_REGEX = None
    ROOM_TAGS = None

    running = False
    task = None
    session = None
    activations = dict()
    hints = None
    forward_bot_messages = None
    deduplication_cache = None
    deduplication_cache_lock = RLock()
    echo_cache = None
    echo_cache_lock = RLock()
    talks_receive_message_queues = dict()
    talks_receive_message_tasks = dict()

    media_cache: Type[MediaCache]

    class Channel(Enum):
        TELEGRAM = 1
        SIGNAL = 2
        WHATSAPP = 3
        MATRIX = 4

    class TimeoutHTTPAdapter(HTTPAdapter):
        fixed_timeout = None

        def __init__(self, fixed_timeout):
            super().__init__()
            self.fixed_timeout = fixed_timeout

        def send(self, request, stream=False, timeout=None, verify=True, cert=None, proxies=None):
            return super().send(request, stream=stream, timeout=self.fixed_timeout, verify=verify, cert=cert, proxies=proxies)

    async def start(self):
        self.log.setLevel(10)  # DEBUG
        self.log.info("PLUGIN START")

        await super().start()
        self.config.load_and_update()

        self.MATRIX_BOT_USER = self.config["matrix_bot_user"]
        self.USER_ID_SKIP_LIST = [self.MATRIX_BOT_USER]
        self.BOT_ON_REGEX = self.config["bot_on_regex"]
        self.BOT_OFF_REGEX = self.config["bot_off_regex"]
        self.ROOM_TAGS = self.config["room_tags"]
        talks_server = self.config["talks_server"]
        talks_protocol = self.config["talks_protocol"]
        talks_port = self.config["talks_port"]
        self.TALKS_BASE_URL = f"{talks_protocol}://{talks_server}:{talks_port}"
        self.TALKS_API_KEY = self.config["talks_api_key"]
        talks_receive_message_path = self.config["talks_receive_message"]
        talks_get_messages_path = self.config["talks_get_messages"]
        talks_confirm_messages_path = self.config["talks_confirm_messages"]
        talks_tag_room_path = self.config["talks_tag_room"]
        self.TALKS_RECEIVE_MESSAGE = f"{self.TALKS_BASE_URL}{talks_receive_message_path}"
        self.TALKS_RECEIVE_MESSAGE_TIMEOUT = self.config["talks_receive_message_timeout"]
        self.TALKS_GET_MESSAGES = f"{self.TALKS_BASE_URL}{talks_get_messages_path}"
        self.TALKS_CONFIRM_MESSAGES = f"{self.TALKS_BASE_URL}{talks_confirm_messages_path}"
        if talks_tag_room_path:
            self.TALKS_TAG_ROOM = f"{self.TALKS_BASE_URL}{talks_tag_room_path}"
        self.hints = self.config["hints"]
        self.forward_bot_messages = self.config["forward_bot_messages"]
        deduplication_cache_size = self.config["deduplication_cache_size"]
        self.deduplication_cache = cachetools.TTLCache(maxsize=deduplication_cache_size, ttl=600)
        echo_cache_size = self.config["echo_cache_size"]
        self.echo_cache = cachetools.TTLCache(maxsize=echo_cache_size, ttl=5)
        fixed_timeout = self.config["fixed_timeout"]

        self.session = requests.Session()
        self.session.mount(self.TALKS_BASE_URL, BridgeBot.TimeoutHTTPAdapter(fixed_timeout))
        self.task = asyncio.create_task(self.message_fetcher_task())

        self.media_cache = MediaCache

        self.running = True

        self.log.info("Task created")

    async def stop(self):
        self.running = False
        await asyncio.wait([self.task])
        tasks = self.talks_receive_message_tasks.copy().values()
        self.talks_receive_message_tasks.clear()
        if len(tasks) > 0:
            await asyncio.wait(tasks)
        await super().stop()
        self.log.info("PLUGIN STOP")

    @classmethod
    def get_config_class(cls) -> Type[BaseProxyConfig]:
        return Config

    def channel(self, user_id: str):
        if user_id.startswith("@telegram_"):
            return self.Channel.TELEGRAM
        elif user_id.startswith("@signal_"):
            return self.Channel.SIGNAL
        elif user_id.startswith("@whatsapp_"):
            return self.Channel.WHATSAPP
        else:
            return self.Channel.MATRIX

    @staticmethod
    def html_format(text: str):
        text = text.replace("\n", "<br/>")
        changed = True
        while changed:
            new_text = re.sub(r'(?<=<pre>)(.*)<br/>(.*)(?=</pre>)', r'\1\n\2', text, flags=re.DOTALL)
            changed = text != new_text
            text = new_text
        return text

    @staticmethod
    def text_format(text: str):
        return re.sub(r"</?[a-zA-Z]+>", "", text)

    @event.on(EventType.ROOM_MESSAGE)
    async def handle_custom_event(self, evt: MessageEvent) -> None:
        """
        Main maubot event handler.
        Calls /receiveMessage Talks endpoint
        :param evt:
        :return:
        """

        if not await self.check_on_off(evt):
            return

        await self.check_room_tags(evt)

        sender_id = evt.sender
        event_id = evt.event_id

        if not self.forward_bot_messages and sender_id in self.USER_ID_SKIP_LIST:
            return

        if self.event_is_duplicated(event_id):
            return

        await self.receive_message(evt, None)

    async def check_on_off(self, evt):
        sender_id = evt.sender
        body = evt.content.body
        room_id = evt.room_id

        if sender_id == self.MATRIX_BOT_USER and not self.event_is_echo(evt):
            if re.match(self.BOT_OFF_REGEX, body, re.IGNORECASE) is not None:
                self.log.info("BOT OFF in room %s", room_id)
                self.activations[room_id] = False
            elif re.match(self.BOT_ON_REGEX, body, re.IGNORECASE) is not None:
                self.log.info("BOT ON in room %s", room_id)
                self.activations[room_id] = True

        if room_id in self.activations:
            return self.activations[room_id]
        else:
            return True

    async def check_room_tags(self, evt):
        if not self.TALKS_TAG_ROOM:
            return

        sender_id = evt.sender
        body = evt.content.body
        room_id = evt.room_id

        if sender_id == self.MATRIX_BOT_USER and not self.event_is_echo(evt):
            for tag_definition in self.ROOM_TAGS:
                regex = tag_definition["regex"]
                tag = tag_definition["tag"]
                value = tag_definition["value"]
                trigger = tag_definition["trigger"]

                if re.match(regex, body, re.IGNORECASE) is not None:
                    await self.tag_room(room_id, tag, value)
                    if trigger is not None:
                        await self.receive_message(evt, trigger)
                    return

    async def tag_room(self, room_id, tag, value):
        self.log.info("setting tag %s=%s for room %s", tag, value, room_id)
        talks_tag_room_request = self.build_talks_tag_room_request(room_id, tag, value)
        talks_tag_room_request_json = jsonpickle.encode(talks_tag_room_request, unpicklable=False)

        try:
            r = await self.post(self.TALKS_TAG_ROOM, talks_tag_room_request_json)
            if 400 <= r.status_code < 500:
                self.log.warning(f"talks_tag_room: status_code={r.status_code} for room_id={room_id}, tag={tag}, value={value}")
            elif r.status_code != 200:
                raise BridgeException(f"status={r.status_code} description={r.json()['description']}")

        except BridgeException as e:
            self.log.error("%s: room tag unsuccessful: %s", self.TALKS_TAG_ROOM, e.message)
        except Exception as e:
            self.log.error("Can not access %s: %s", self.TALKS_TAG_ROOM, e)

    @staticmethod
    def build_talks_tag_room_request(room_id, tag, value):
        return TalksTagRoomRequest(room_id, tag, value)

    async def receive_message(self, evt, body):
        self.talks_receive_message_enqueue(evt, body)

    def talks_receive_message_enqueue(self, evt, body):
        room_id = evt.room_id
        queue = self.get_talks_receive_message_queue(room_id)
        queue.appendleft([evt, body])

    def get_talks_receive_message_queue(self, room_id):
        if room_id not in self.talks_receive_message_queues:
            self.talks_receive_message_queues[room_id] = deque()
            self.create_talks_receive_message_task(room_id)
        return self.talks_receive_message_queues[room_id]

    def create_talks_receive_message_task(self, room_id):
        if room_id not in self.talks_receive_message_tasks:
            task = asyncio.create_task(self.talks_receive_message_per_room_task(room_id))
            self.talks_receive_message_tasks[room_id] = task

    async def talks_receive_message_per_room_task(self, room_id):
        while room_id in self.talks_receive_message_tasks:
            if room_id in self.talks_receive_message_queues:
                queue = self.get_talks_receive_message_queue(room_id)
                if len(queue) > 0:
                    evt, body = queue[-1]
                    sent = False
                    i = 0
                    delay = 0.1 * 2 ** i
                    while room_id in self.talks_receive_message_tasks and delay <= self.TALKS_RECEIVE_MESSAGE_TIMEOUT:
                        try:
                            await self.do_receive_message(evt, body)
                            queue.pop()
                            sent = True
                            break
                        except BridgeException as e:
                            i = i + 1
                            delay = 0.1 * 2 ** i
                            self.log.warning("%s: message %s failed Talks sending, will retry in %s seconds: %s", self.TALKS_RECEIVE_MESSAGE, evt.event_id, delay, e.message)
                            await asyncio.sleep(delay)
                    if not sent:
                        self.log.error("%s: message %s failed Talks sending and discarded after exceeding %s seconds",self.TALKS_RECEIVE_MESSAGE, evt.event_id, self.TALKS_RECEIVE_MESSAGE_TIMEOUT)
                else:
                    del self.talks_receive_message_queues[room_id]
                    del self.talks_receive_message_tasks[room_id]
            await asyncio.sleep(0.1)

    async def do_receive_message(self, evt, body):
        from requests import exceptions as requests_exceptions
        from urllib3 import exceptions as urllib3_exceptions
        event_id = evt.event_id
        talks_receive_message_request = await self.build_talks_receive_message_request(evt, body)
        if talks_receive_message_request is None:
            return
        talks_receive_message_request_json = jsonpickle.encode(talks_receive_message_request, unpicklable=False)
        # self.log.debug("ReceiveMessage request: %s", talks_receive_message_request_json)
        try:
            r = await self.post(self.TALKS_RECEIVE_MESSAGE, talks_receive_message_request_json)
            if 400 <= r.status_code < 500:
                self.log.warning(f"talks_receive_message: status_code={r.status_code} for event_id={event_id}")
            elif r.status_code != 200:
                raise BridgeException(f"status={r.status_code} description={r.json()['description']}")

            await evt.mark_read()
            # self.log.debug("ReceiveMessage response: %s", r.text)

        except BridgeException as e:
            raise e
        except ConnectionError as e:
            raise BridgeException("ConnectionError")
        except requests_exceptions.ConnectionError as e:
            raise BridgeException("requests::ConnectionError")
        except urllib3_exceptions.ConnectionError as e:
            raise BridgeException(f"urllib3::{e.message}")
        except NewConnectionError as e:
            raise BridgeException("NewConnectionError")
        except Exception as e:
            self.log.error("Can not access %s, message %s discarded: [%s] %s", self.TALKS_RECEIVE_MESSAGE, event_id, e.__class__.__name__, e)

    def event_is_echo(self, evt: MessageEvent) -> bool:
        echoed = False
        sender_id = evt.sender
        body = self.get_evt_cache_body(evt)

        if body is None:
            return False

        body_hash = hash(body)

        if sender_id == self.MATRIX_BOT_USER:

            self.echo_cache_lock.acquire()

            if self.echo_cache.get(body_hash) is not None:
                # self.log.debug("echo cache hit for %s", body)
                echoed = True

            self.echo_cache_lock.release()

        else:
            self.cache_body(body, body_hash)

        return echoed

    def get_evt_cache_body(self, evt: Event):
        if evt is MessageEvent:
            return evt.content.body
        elif evt is RedactionEvent:
            return evt.content
        else:
            return evt.content.body if hasattr(evt, "content") and hasattr(evt.content, "body") else None

    def cache_body(self, body, url=None, body_hash=None):
        if body_hash is None:
            body_hash = hash(body)
        if url is not None:
            body_hash = f"url::body_hash"

        self.echo_cache_lock.acquire()

        self.echo_cache[body_hash] = True

        self.echo_cache_lock.release()

    def event_is_duplicated(self, event_id) -> bool:
        duplicated = False

        self.deduplication_cache_lock.acquire()

        if self.deduplication_cache.get(event_id) is not None:
            # self.log.debug("deduplication cache hit for %s", event_id)
            duplicated = True
        else:
            self.deduplication_cache[event_id] = True
            # self.log.debug("deduplication cache set for %s", event_id)

        self.deduplication_cache_lock.release()

        return duplicated

    async def build_talks_receive_message_request(self, evt, body=None):
        sender_id = evt.sender
        room_id = evt.room_id
        event_id = evt.event_id
        timestamp = evt.timestamp
        event_type = f"{evt.type}"
        content = evt.content
        message_type = content.msgtype

        message_format = None
        message_formatted_body = None
        message_geo_uri = None

        mime_type = None
        url = None
        base64bytes = None

        built = False

        if message_type == MessageType.TEXT:  # intentionally ignore MessageType.NOTICE and MessageType.EMOTE
            content: TextMessageEventContent = evt.content
            if body is None:
                body = content.body
            self.log.debug(f"incoming message: {message_type}: {body}")
            message_format = f"{content.format}"
            message_formatted_body = content.formatted_body
            built = True
        elif message_type == MessageType.LOCATION:
            content: LocationMessageEventContent = evt.content
            self.log.debug(f"incoming message: {message_type}: {content.geo_uri}")
            if body is None:
                body = content.body
            message_geo_uri = content.geo_uri
            built = True
        elif message_type in (MessageType.IMAGE, MessageType.VIDEO, MessageType.AUDIO, MessageType.FILE):
            content: MediaMessageEventContent = evt.content
            mime_type = content.info.mimetype if hasattr(content.info, "mimetype") else None
            url = content.url
            self.log.debug(f"incoming message: {message_type} with MIME: {mime_type} and mxcUri: {url}")
            if url is not None:
                downloaded_bytes = await self.download_media_content(url)
                base64bytes = base64.b64encode(downloaded_bytes) if downloaded_bytes else None
                built = True
            else:
                self.log.warning(f"{message_type} URL is not available, skipping sending to Talks (roomId={room_id}, sender_id={sender_id}, event_id={event_id})")

        if built:
            return TalksReceiveMessageRequest(timestamp, room_id, event_id, sender_id, event_type, body,
                                              message_type, message_format, message_formatted_body, message_geo_uri,
                                              mime_type, url, base64bytes)
        else:
            return None

    async def download_media_content(self, url) -> Optional[bytes]:
        if url:
            self.log.debug(f"download_media_content: going to download bytes from {url}")
            downloaded_bytes = await self.client.download_media(url)
            self.log.debug(f"download_media_content: downloaded bytes from {url}.")
            return downloaded_bytes
        else:
            return None

    async def message_fetcher_task(self):
        """
        Sole task that moves messages from Talks to Matrix.
        Calls the Talks Hippy endpoints /getMessages and /confirmMessages (by calling functions)
        :return:
        """
        self.log.info("Started message_fetcher_task")

        while self.running:
            # self.log.debug("LOOP start_message_fetcher")
            messages = await self.fetch_messages()
            if messages is not None:
                message_ids = await self.propagate_messages(messages)
                await self.confirm_messages(message_ids)
            message_fetcher_delay = self.config["message_fetcher_delay"]
            await asyncio.sleep(message_fetcher_delay)

        self.log.info("Stopped message_fetcher_task")

    async def fetch_messages(self) -> object:
        messages = None

        try:
            r = await self.get(self.TALKS_GET_MESSAGES)
            if 400 <= r.status_code < 500:
                self.log.warning(f"talks_get_messages: status_code={r.status_code}")
            elif r.status_code != 200:
                raise BridgeException(f"status={r.status_code} description={r.json()['description']}")
            else:
                # self.log.debug("GetMessages response: %s", r.text)
                messages = r.json()["messages"]

        except BridgeException as e:
            self.log.error("%s: %s, will retry.", self.TALKS_GET_MESSAGES, e.message)
        except Exception as e:
            self.log.error("Can not access %s, will retry: %s", self.TALKS_GET_MESSAGES, e)

        return messages

    async def propagate_messages(self, messages):
        if len(messages) == 0:
            return list()

        messages_per_room = defaultdict(list)
        for message in messages:
            room_id = message["roomId"]
            messages_per_room[room_id].append(message)

        tasks = list()
        for room_id, messages_in_room in messages_per_room.items():
            task = asyncio.create_task(self.message_propagator_per_room_task(room_id, messages_in_room))
            tasks.append(task)

        done, pending = await asyncio.wait(tasks)
        results = [task.result() for task in done]
        id_triples = [triple for triples in results for triple in triples]

        return id_triples

    async def message_propagator_per_room_task(self, room_id, messages):
        id_triples = []

        for idx, message in enumerate(messages):
            if idx > 0:
                message_propagator_delay = self.config["message_propagator_delay"]
                await asyncio.sleep(message_propagator_delay)

            event_id, url = await self.propagate_message(message)
            id_triples.append((message["id"], event_id, url))

        return id_triples

    async def propagate_message(self, message):
        event_id = None
        event_type: EventType = EventType.ROOM_MESSAGE
        content, url = await self.build_message_content(message)
        actions = message["actions"]

        if content is not None:
            try:
                event_id = await self.client.send_message_event(message["roomId"], event_type, content)
                self.log.debug("Propagated message %s -> %s", message["id"], event_id)
            except Exception as e:
                self.log.error("Can not propagate message %s, propagation cancelled: %s", message["id"], e)

        if self.hints and actions is not None and len(actions) > 0:
            try:
                hints_content = await self.build_hints_content(actions)
                hints_delay = self.config["hints_delay"]
                await asyncio.sleep(hints_delay)
                await self.client.send_message_event(message["roomId"], event_type, hints_content)
                self.log.debug("Sent hints for message %s", message["id"])
            except Exception as e:
                self.log.error("Can not send hints for message %s, propagation cancelled: %s", message["id"], e)

        return event_id, url

    async def build_message_content(self, message):
        if message is None:
            pass

        body_log = message["body"] if message["body"] and message["messageType"] and (message["messageType"] == "m.text") else "N/A"
        self.log.debug(f"outgoing message: roomId=[{message['roomId']}] messageType=[{message['messageType']}] bodyType=[{message['bodyType']}] mimeType=[{message['mimeType']}] body=[{body_log}]")

        content = None
        built = False

        body_type = message["bodyType"]
        url = None

        if body_type == "TEXT":
            content = TextMessageEventContent(msgtype=MessageType.NOTICE, body=message["body"])
            built = True

        elif body_type == "HTML":
            content = TextMessageEventContent(msgtype=MessageType.NOTICE, body=message["body"])
            content.format = Format.HTML
            formatted_body = self.html_format(content.body)
            content.body, content.formatted_body = await parse_formatted(
                formatted_body, render_markdown=False, allow_html=True
            )
            built = True

        elif body_type == "GEO_URI":
            content = LocationMessageEventContent(msgtype=MessageType.LOCATION, geo_uri=message["body"])
            built = True

        elif body_type in ("IMAGE", "AUDIO", "VIDEO", "FILE"):
            url = message["mxcUri"]
            base64bytes = message["body"]
            if base64bytes is None and url is not None:
                try:
                    raw_bytes = await self.download_media_content(url)
                    info = await self._upload_and_get_media_info(body_type, "filename", raw_bytes, uri=url)
                    self.log.debug(f"outgoing message: mxc_uri (pre-existing): {url}")
                    content = MediaMessageEventContent(url=url, body="filename",
                                                       msgtype=self.build_message_type(body_type),
                                                       info=await self.build_media_info(body_type, info))
                    built = True
                except Exception as e:
                    self.log.error("Can not download %s content from URI %s for message %s, propagation cancelled: %s",
                                   body_type, url, message["id"], e)
            elif base64bytes is not None:
                try:
                    raw_bytes = base64.b64decode(base64bytes)
                    filename = message["filename"]
                    info = await self._upload_and_get_media_info(body_type, filename, raw_bytes)
                    url = info.mxc_uri
                    self.log.debug(f"outgoing message: mxc_uri (new): {url}")
                    content = MediaMessageEventContent(url=url, body=info.file_name,
                                                       msgtype=self.build_message_type(body_type),
                                                       info=await self.build_media_info(body_type, info))
                    built = True
                except Exception as e:
                    self.log.error("Can not upload %s content for message %s, propagation cancelled: %s",
                                   body_type, message["id"], e)
            else:
                raise Exception("Empty body in Talks response")

        elif body_type == 'DELETE_MESSAGE':
            room_id = message["roomId"]
            message_id = message["body"]
            try:
                redact_evt_id = await self.client.redact(
                    room_id,
                    message_id,
                    reason="Message removed by bot",
                )
                redact_evt = await self.client.get_event(room_id, redact_evt_id)
                redact_content = redact_evt["content"]
                self.cache_body(redact_content)
                built = True
            except Exception as e:
                self.log.error("Can not redact message %s in room %s from DELETE_MESSAGE %s",
                               message_id, room_id, message["id"], e)

        if built and content is not None:
            self.cache_body(content["body"], url=url)

        return content, url

    def build_message_type(self, body_type):
        if body_type == "IMAGE":
            return MessageType.IMAGE
        elif body_type == "AUDIO":
            return MessageType.AUDIO
        elif body_type == "VIDEO":
            return MessageType.VIDEO
        elif body_type == "FILE":
            return MessageType.FILE
        else:
            return None

    async def build_media_info(self, body_type, info):
        if body_type == "IMAGE":
            return ImageInfo(
                mimetype=info.mime_type,
                size=info.size,
                width=info.width,
                height=info.height,
            )
        elif body_type == "AUDIO":
            return AudioInfo(
                mimetype=info.mime_type,
                size=info.size,
                duration=info.duration
            )
        elif body_type == "VIDEO":
            return VideoInfo(
                mimetype=info.mime_type,
                size=info.size,
                width=info.width,
                height=info.height,
                duration=info.duration
            )
        elif body_type == "FILE":
            return FileInfo(
                mimetype=info.mime_type,
                size=info.size
            )
        else:
            return {}

    async def _upload_and_get_media_info(self, type: str, file_name: str, data: bytes, uri=None) -> MediaCache:
        width = height = duration = mime_type = None
        if magic is not None:
            mime_type = magic.from_buffer(data, mime=True)
        if type == "IMAGE":
            width, height = self._get_image_info(data)
        elif type == "AUDIO":
            duration = self._get_audio_info(data)
        elif type == "VIDEO":
            width, height, duration = self._get_video_info(data)
        elif type == "FILE":
            mime_type = self._get_file_info(data)
        if uri is None:
            uri = await self.client.upload_media(data, mime_type=mime_type)
        cache = self.media_cache(mxc_uri=uri, file_name=file_name,
                                 mime_type=mime_type, width=width, height=height,
                                 duration=duration,
                                 size=len(data))
        return cache

    def _get_image_info(self, data: bytes):
        width = height = None
        if Image is not None:
            image = Image.open(BytesIO(data))
            width, height = image.size
        return width, height

    def _get_audio_info(self, data: bytes):
        duration = None
        # TODO get audio duration
        return duration

    def _get_video_info(self, data: bytes):
        width = height = duration = None
        # TODO
        return width, height, duration

    def _get_file_info(self, data: bytes):
        mime_type = None
        # TODO
        return mime_type

    async def build_hints_content(self, actions):
        hints = "<b>Options</b>:"
        for hint, text in actions.items():
            hints = hints + f"\n<b>{hint}</b> : {text}"

        content = TextMessageEventContent(msgtype=MessageType.NOTICE, body=hints)
        content.format = Format.HTML
        formatted_body = self.html_format(content.body)
        content.body, content.formatted_body = await parse_formatted(
            formatted_body, render_markdown=False, allow_html=True
        )

        self.cache_body(content.body)

        return content

    async def confirm_messages(self, message_ids):
        if len(message_ids) > 0:
            talks_confirm_messages_request = self.build_talks_confirm_messages_request(message_ids)
            talks_confirm_messages_request_json = jsonpickle.encode(talks_confirm_messages_request, unpicklable=False)
            self.log.debug("ConfirmMessages request: %s", talks_confirm_messages_request_json)

            try:
                r = await self.post(self.TALKS_CONFIRM_MESSAGES, talks_confirm_messages_request_json)
                if 400 <= r.status_code < 500:
                    self.log.warning(f"talks_confirm_messages: status_code={r.status_code} for message_ids={','.join(message_ids)}")
                elif r.status_code != 200:
                    raise BridgeException(f"status={r.status_code} description={r.json()['description']}")
                else:
                    self.log.debug("ConfirmMessages response: %s", r.text)

            except BridgeException as e:
                self.log.error("%s: %s, will retry.", self.TALKS_CONFIRM_MESSAGES, e.message)
            except Exception as e:
                self.log.error("Can not access %s, will retry: %s", self.TALKS_CONFIRM_MESSAGES, e)

    @staticmethod
    def build_talks_confirm_messages_request(message_ids) -> TalksConfirmMessageRequest:
        messages = []

        for id_triple in message_ids:
            message = TalksConfirmMessageRequest.Message(id_triple[0], id_triple[1], id_triple[2])
            messages.append(message)

        return TalksConfirmMessageRequest(messages)

    async def get(self, url):
        headers = {"Authorization": f"Bearer {self.TALKS_API_KEY}"}
        loop = asyncio.get_event_loop()
        r = await loop.run_in_executor(None, functools.partial(self.session.get, url, headers=headers))
        return r

    async def post(self, url, json_contents):
        headers = {"Authorization": f"Bearer {self.TALKS_API_KEY}"}
        loop = asyncio.get_event_loop()
        r = await loop.run_in_executor(None, functools.partial(self.session.post, url, json=json_contents,
                                                               headers=headers))
        return r
