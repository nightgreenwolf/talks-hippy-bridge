from mautrix.util.config import BaseProxyConfig, ConfigUpdateHelper


class Config(BaseProxyConfig):
    def do_update(self, helper: ConfigUpdateHelper) -> None:
        helper.copy("matrix_bot_user")
        helper.copy("talks_server")
        helper.copy("talks_port")
        helper.copy("talks_protocol")
        helper.copy("talks_receive_message")
        helper.copy("talks_get_messages")
        helper.copy("talks_confirm_messages")
        helper.copy("bot_on_regex")
        helper.copy("bot_off_regex")
        helper.copy("hints")
        helper.copy("deduplication_cache_size")
        helper.copy("fixed_timeout")
        helper.copy("message_fetcher_delay")
        helper.copy("message_propagator_delay")
        helper.copy("hints_delay")
