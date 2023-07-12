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
import re
from collections import defaultdict
from enum import Enum
from threading import RLock
from typing import Type

import cachetools
import jsonpickle
import requests
from config import Config
from maubot import Plugin, MessageEvent
from maubot.handlers import event
from maubot.matrix import parse_formatted
from mautrix.types import EventType, TextMessageEventContent, MessageType, Format, LocationMessageEventContent, \
    MediaMessageEventContent
from mautrix.util.config import BaseProxyConfig
from requests.adapters import HTTPAdapter


class TalksReceiveMessageRequest:
    def __init__(self, timestamp, room_id, event_id, sender_id, event_type, body, message_type,
                 body_format, formatted_body, geo_uri):
        self.timestamp = timestamp
        self.roomId = room_id
        self.eventId = event_id
        self.senderId = sender_id
        self.eventType = event_type
        self.body = body
        self.messageType = message_type
        self.format = body_format
        self.formattedBody = formatted_body
        self.geoUri = geo_uri


class TalksConfirmMessageRequest:

    class Message:
        def __init__(self, source_id, matrix_id):
            self.sourceId = source_id
            self.matrixId = matrix_id

    def __init__(self, messages):
        self.messages = messages


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
    TALKS_RECEIVE_MESSAGE = None
    TALKS_GET_MESSAGES = None
    TALKS_CONFIRM_MESSAGES = None

    BOT_ON_REGEX = None
    BOT_OFF_REGEX = None

    running = False
    task = None
    session = None
    activations = dict()
    hints = None
    deduplication_cache = None
    deduplication_cache_lock = RLock()
    echo_cache = None
    echo_cache_lock = RLock()

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

        self.MATRIX_BOT_USER = self.config["matrix_bot_user"]
        self.USER_ID_SKIP_LIST = [self.MATRIX_BOT_USER]
        self.BOT_ON_REGEX = self.config["bot_on_regex"]
        self.BOT_OFF_REGEX = self.config["bot_off_regex"]
        talks_server = self.config["talks_server"]
        talks_protocol = self.config["talks_protocol"]
        talks_port = self.config["talks_port"]
        self.TALKS_BASE_URL = f"{talks_protocol}://{talks_server}:{talks_port}/"
        talks_receive_message_path = self.config["talks_receive_message"]
        talks_get_messages_path = self.config["talks_get_messages"]
        talks_confirm_messages_path = self.config["talks_confirm_messages"]
        self.TALKS_RECEIVE_MESSAGE = f"{self.TALKS_BASE_URL}{talks_receive_message_path}"
        self.TALKS_GET_MESSAGES = f"{self.TALKS_BASE_URL}{talks_get_messages_path}"
        self.TALKS_CONFIRM_MESSAGES = f"{self.TALKS_BASE_URL}{talks_confirm_messages_path}"
        self.hints = self.config["hints"]
        deduplication_cache_size = self.config["deduplication_cache_size"]
        self.deduplication_cache = cachetools.TTLCache(maxsize=deduplication_cache_size, ttl=600)
        echo_cache_size = self.config["echo_cache_size"]
        self.echo_cache = cachetools.TTLCache(maxsize=echo_cache_size, ttl=5)
        fixed_timeout = self.config["fixed_timeout"]

        self.session = requests.Session()
        self.session.mount(self.TALKS_BASE_URL, BridgeBot.TimeoutHTTPAdapter(fixed_timeout))
        self.task = asyncio.create_task(self.message_fetcher_task())
        self.running = True

        self.log.info("Task created")

    async def stop(self):
        self.running = False
        await asyncio.wait([self.task])
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

        sender_id = evt.sender
        event_id = evt.event_id

        if sender_id in self.USER_ID_SKIP_LIST:
            return

        if self.event_is_duplicated(event_id):
            return

        talks_receive_message_request = self.build_talks_receive_message_request(evt)
        talks_receive_message_request_json = jsonpickle.encode(talks_receive_message_request, unpicklable=False)
        # self.log.debug("ReceiveMessage request: %s", talks_receive_message_request_json)

        try:
            loop = asyncio.get_event_loop()
            r = await loop.run_in_executor(None, self.session.post, self.TALKS_RECEIVE_MESSAGE, None,
                                           talks_receive_message_request_json)
            if r.status_code != 200:
                raise BridgeException(f"status={r.status_code} description={r.json()['description']}")

            await evt.mark_read()
            # self.log.debug("ReceiveMessage response: %s", r.text)

        except BridgeException as e:
            self.log.error("%s: message %s discarded: %s", self.TALKS_RECEIVE_MESSAGE, event_id, e.message)
        except Exception as e:
            self.log.error("Can not access %s, message %s discarded: %s", self.TALKS_RECEIVE_MESSAGE, event_id, e)

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

    def event_is_echo(self, evt: MessageEvent) -> bool:
        echoed = False
        sender_id = evt.sender
        body = evt.content.body

        if sender_id == self.MATRIX_BOT_USER:

            self.echo_cache_lock.acquire()

            if self.echo_cache.get(body) is not None:
                # self.log.debug("echo cache hit for %s", body)
                echoed = True

            self.echo_cache_lock.release()

        else:
            self.cache_body(body)

        return echoed

    def cache_body(self, body):
        self.echo_cache_lock.acquire()

        self.echo_cache[body] = True

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

    @staticmethod
    def build_talks_receive_message_request(evt):
        sender_id = evt.sender
        room_id = evt.room_id
        event_id = evt.event_id
        timestamp = evt.timestamp
        event_type = f"{evt.type}"
        content = evt.content
        body = content.body
        message_type = f"{content.msgtype}"

        message_format = None
        message_formatted_body = None
        message_geo_uri = None

        if message_type in ("m.text", "m.notice", "m.emote"):
            message_format = f"{content.format}"
            message_formatted_body = content.formatted_body
        elif message_type == "m.location":
            message_geo_uri = content.geo_uri

        return TalksReceiveMessageRequest(timestamp, room_id, event_id, sender_id, event_type, body,
                                          message_type, message_format, message_formatted_body, message_geo_uri)

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
            loop = asyncio.get_event_loop()
            r = await loop.run_in_executor(None, self.session.get, self.TALKS_GET_MESSAGES)
            if r.status_code != 200:
                raise BridgeException(f"status={r.status_code} description={r.json()['description']}")

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
        id_pairs = [pair for pairs in results for pair in pairs]

        return id_pairs

    async def message_propagator_per_room_task(self, room_id, messages):
        id_pairs = []

        for idx, message in enumerate(messages):
            if idx > 0:
                message_propagator_delay = self.config["message_propagator_delay"]
                await asyncio.sleep(message_propagator_delay)

            event_id = await self.propagate_message(message)
            id_pairs.append((message["id"], event_id))

        return id_pairs

    async def propagate_message(self, message):
        event_id = None
        event_type: EventType = EventType.ROOM_MESSAGE
        content = await self.build_message_content(message)
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

        return event_id

    async def build_message_content(self, message):
        self.log.info(f"build_message_content: message: {message}")
        content = None

        if message is None:
            pass

        elif message["bodyType"] == "TEXT":
            content = TextMessageEventContent(msgtype=MessageType.NOTICE, body=message["body"])

        elif message["bodyType"] == "HTML":
            content = TextMessageEventContent(msgtype=MessageType.NOTICE, body=message["body"])
            content.format = Format.HTML
            formatted_body = self.html_format(content.body)
            content.body, content.formatted_body = await parse_formatted(
                formatted_body, render_markdown=False, allow_html=True
            )

        elif message["bodyType"] == "GEO_URI":
            content = LocationMessageEventContent(msgtype=MessageType.LOCATION, geo_uri=message["body"])

        elif message["bodyType"] == "IMAGE":
            try:
                base64bytes = message["body"]
                if base64bytes is not None:
                    raw_bytes = base64.b64decode(base64bytes)
                    mime_type = message["mimeType"]
                    filename = message["filename"]
                    mxc_uri = await self.client.upload_media(data=raw_bytes, mime_type=mime_type, filename=filename,
                                                             async_upload=True)
                    content = MediaMessageEventContent(msgtype=MessageType.IMAGE, url=mxc_uri)
                else:
                    raise Exception("Empty body in Talks response")
            except Exception as e:
                self.log.error("Can not upload %s content for message %s, propagation cancelled: %s",
                               message["bodyType"], message["id"], e)

        self.cache_body(content.body)

        return content

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
                loop = asyncio.get_event_loop()
                r = await loop.run_in_executor(None, self.session.post, self.TALKS_CONFIRM_MESSAGES, None,
                                               talks_confirm_messages_request_json)
                if r.status_code != 200:
                    raise BridgeException(f"status={r.status_code} description={r.json()['description']}")

                self.log.debug("ConfirmMessages response: %s", r.text)

            except BridgeException as e:
                self.log.error("%s: %s, will retry.", self.TALKS_CONFIRM_MESSAGES, e.message)
            except Exception as e:
                self.log.error("Can not access %s, will retry: %s", self.TALKS_CONFIRM_MESSAGES, e)

    @staticmethod
    def build_talks_confirm_messages_request(message_ids) -> TalksConfirmMessageRequest:
        messages = []

        for id_pair in message_ids:
            message = TalksConfirmMessageRequest.Message(id_pair[0], id_pair[1])
            messages.append(message)

        return TalksConfirmMessageRequest(messages)
