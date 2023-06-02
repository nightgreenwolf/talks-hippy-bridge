import asyncio
import re
from enum import Enum
from threading import RLock

import cachetools
import jsonpickle
import requests
from maubot import Plugin, MessageEvent
from maubot.handlers import event
from maubot.matrix import parse_formatted
from mautrix.types import EventType, TextMessageEventContent, MessageType, Format

MATRIX_BOT_USER = "@bot:example.com"  # TODO move this to bot configuration
USER_ID_SKIP_LIST = [
    MATRIX_BOT_USER,
]

TALKS_SERVER = "192.168.1.145"  # localhost
TALKS_PORT = 8080
TALKS_RECEIVE_MESSAGE = f"http://{TALKS_SERVER}:{TALKS_PORT}/matrix/receiveMessage"
TALKS_GET_MESSAGES = f"http://{TALKS_SERVER}:{TALKS_PORT}/matrix/getMessages"
TALKS_CONFIRM_MESSAGES = f"http://{TALKS_SERVER}:{TALKS_PORT}/matrix/confirmMessages"


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

    running = True
    cache = cachetools.TTLCache(maxsize=1024, ttl=600)
    cachelock = RLock()

    class Channel(Enum):
        TELEGRAM = 1
        SIGNAL = 2
        WHATSAPP = 3
        MATRIX = 4

    async def start(self):
        self.log.setLevel(10)
        self.log.info("PLUGIN START")
        await self.start_message_fetcher()

    async def stop(self):
        self.log.info("PLUGIN STOP")
        self.running = False

    async def pre_stop(self):
        self.log.info("PLUGIN PRE STOP")
        self.running = False

    def channel(self, user_id: str):
        if user_id.startswith("@telegram_"):
            return self.Channel.TELEGRAM
        elif user_id.startswith("@signal_"):
            return self.Channel.SIGNAL
        elif user_id.startswith("@whatsapp_"):
            return self.Channel.WHATSAPP
        else:
            return self.Channel.MATRIX

    def html_format(self, channel: Channel, text: str):
        if channel == self.Channel.TELEGRAM:
            return text.replace("\n", "<br/>")
        elif channel == self.Channel.SIGNAL:
            return self.text_format(text).replace("\n", "<br/>")
        elif channel == self.Channel.WHATSAPP:
            return text.replace("\n", "<br/>")
        else:  # matrix
            return text.replace("\n", "<br/>")

    @staticmethod
    def text_format(text: str):
        return re.sub(r"</?[a-zA-Z]+>", "", text)

    @event.on(EventType.ROOM_MESSAGE)
    async def handle_custom_event(self, evt: MessageEvent) -> None:
        # self.log.info("Custom event data: %s", evt.content)

        sender_id = evt.sender
        event_id = evt.event_id

        if sender_id in USER_ID_SKIP_LIST:
            return

        if self.duplicated_event(event_id):
            return

        talks_receive_message_request = self.build_talks_receive_message_request(evt)
        talks_receive_message_request_json = jsonpickle.encode(talks_receive_message_request, unpicklable=False)
        # self.log.debug("ReceiveMessage request: %s", talks_receive_message_request_json)

        try:
            r = requests.post(TALKS_RECEIVE_MESSAGE, json=talks_receive_message_request_json)
            if r.status_code != 200:
                raise BridgeException(f"status={r.status_code} description={r.json()['description']}")

            await evt.mark_read()
            # self.log.debug("ReceiveMessage response: %s", r.text)

        except BridgeException as e:
            self.log.error("%s: message %s discarded: %s", TALKS_RECEIVE_MESSAGE, event_id, e.message)
        except:
            self.log.error("Can not access %s, message %s discarded", TALKS_RECEIVE_MESSAGE, event_id)

    def duplicated_event(self, event_id) -> bool:
        duplicated = False

        self.cachelock.acquire()

        if self.cache.get(event_id) is not None:
            # self.log.debug("deduplication cache hit for %s", event_id)
            duplicated = True
        else:
            self.cache[event_id] = True
            # self.log.debug("deduplication cache set for %s", event_id)

        self.cachelock.release()

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
            message_format = content.format
            message_formatted_body = content.formatted_body
        elif message_type == "m.location":
            message_geo_uri = content.geo_uri

        return TalksReceiveMessageRequest(timestamp, room_id, event_id, sender_id, event_type, body,
                                          message_type, message_format, message_formatted_body, message_geo_uri)

    async def start_message_fetcher(self):
        self.log.info("START start_message_fetcher")

        while self.running:
            # self.log.debug("LOOP start_message_fetcher")
            messages = self.fetch_messages()
            if messages is not None:
                message_ids = await self.propagate_messages(messages)
                self.confirm_messages(message_ids)
            await asyncio.sleep(0.2)

        self.log.info("END LOOP start_message_fetcher")

    def fetch_messages(self) -> object:
        messages = None

        try:
            r = requests.get(TALKS_GET_MESSAGES)
            if r.status_code != 200:
                raise BridgeException(f"status={r.status_code} description={r.json()['description']}")

            # self.log.debug("GetMessages response: %s", r.text)
            messages = r.json()["messages"]

        except BridgeException as e:
            self.log.error("%s: %s, will retry.", TALKS_GET_MESSAGES, e.message)
        except:
            self.log.error("Can not access %s, will retry", TALKS_GET_MESSAGES)

        return messages

    async def propagate_messages(self, messages):
        id_pairs = []

        for idx, message in enumerate(messages):
            if idx > 0:
                await asyncio.sleep(0.1)

            event_id = await self.propagate_message(message)
            # self.log.debug("Propagated message %s -> %s", message["id"], event_id)
            id_pairs.append((message["id"], event_id))

        return id_pairs

    async def propagate_message(self, message):
        event_id = None
        event_type: EventType = EventType.ROOM_MESSAGE
        # content = await self.build_message_content(message)
        content = await self.build_message_content(message)

        if content is not None:
            try:
                event_id = await self.client.send_message_event(message["roomId"], event_type, content)
            except:
                self.log.error("Can not propagate message %s, propagation cancelled", message["id"])

        return event_id

    async def build_message_content(self, message):
        content = None

        if message["bodyType"] == "HTML":
            content = TextMessageEventContent(msgtype=MessageType.NOTICE, body=message["body"])
            content.format = Format.HTML
            content.body, content.formatted_body = await parse_formatted(
                content.body, render_markdown=False, allow_html=True
            )
        elif message["bodyType"] == "GEO_URI":  # TODO
            self.log.info("Can not build a GEO_URI message: %s", message["body"])

        return content

    def confirm_messages(self, message_ids):
        if len(message_ids) > 0:
            talks_confirm_messages_request = self.build_talks_confirm_messages_request(message_ids)
            talks_confirm_messages_request_json = jsonpickle.encode(talks_confirm_messages_request, unpicklable=False)
            # self.log.debug("ConfirmMessages request: %s", talks_confirm_messages_request_json)

            try:
                r = requests.post(TALKS_CONFIRM_MESSAGES, json=talks_confirm_messages_request_json)
                if r.status_code != 200:
                    raise BridgeException(f"status={r.status_code} description={r.json()['description']}")

                # self.log.debug("ConfirmMessages response: %s", r.text)

            except BridgeException as e:
                self.log.error("%s: %s, will retry.", TALKS_CONFIRM_MESSAGES, e.message)
            except:
                self.log.error("Can not access %s, will retry", TALKS_CONFIRM_MESSAGES)

    @staticmethod
    def build_talks_confirm_messages_request(message_ids) -> TalksConfirmMessageRequest:
        messages = []

        for id_pair in message_ids:
            message = TalksConfirmMessageRequest.Message(id_pair[0], id_pair[1])
            messages.append(message)

        return TalksConfirmMessageRequest(messages)
