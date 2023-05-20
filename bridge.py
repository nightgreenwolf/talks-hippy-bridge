import re
from enum import Enum
from html import escape

from maubot import Plugin, MessageEvent
from maubot.handlers import event
from mautrix.types import TextMessageEventContent, MessageType, Format, RelatesTo, RelationType, EventType

MATRIX_BOT_USER = "@bot:logicas.org"  # TODO move this to bot configuration
USER_ID_SKIP_LIST = [
    MATRIX_BOT_USER,
    "@telegram_52504489:logicas.org",  # @userinfobot
    "@telegram_1272441549:logicas.org"
]


class BridgeBot(Plugin):
    class Channel(Enum):
        TELEGRAM = 1
        SIGNAL = 2
        WHATSAPP = 3
        MATRIX = 4

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

        if sender_id in USER_ID_SKIP_LIST:
            return

        channel = self.channel(sender_id)

        room_id = evt.room_id
        event_id = evt.event_id
        timestamp = evt.timestamp
        event_type = evt.type
        content = evt.content
        body = content.body
        message_type = content.msgtype

        message_format = None
        message_formatted_body = None
        message_geo_uri = None

        if message_type in ["m.text", "m.notice", "m.emote"]:
            message_format = content.format
            message_formatted_body = content.formatted_body
        elif message_type == "m.location":
            message_geo_uri = content.geo_uri

        input_info = f"<b>INPUT_EVENT</b> <i>INFO</i> <code>code</code>\n" \
                     f"channel = {channel}\n" \
                     f"room_id = {room_id}\n" \
                     f"event_id = {event_id}\n" \
                     f"sender_id = {sender_id}\n" \
                     f"timestamp = {timestamp}\n" \
                     f"event_type = {event_type}\n" \
                     f"body = {escape(body)}\n" \
                     f"message_type = {message_type}\n" \
                     f"message_format = {message_format}\n" \
                     f"message_formatted_body = {message_formatted_body}\n" \
                     f"message_geo_uri = {message_geo_uri}"

        output_evt_content = TextMessageEventContent(
            msgtype=MessageType.NOTICE,
            format=Format.HTML,
            body=self.text_format(input_info),
            formatted_body=self.html_format(channel, input_info),
            relates_to=RelatesTo(
                rel_type=RelationType("xyz.maubot.talks_bridge.xray"),
                event_id=evt.event_id,
            ))

        await evt.respond(output_evt_content)
