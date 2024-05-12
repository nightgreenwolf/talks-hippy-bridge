# talks-bridge

Bridges a Matrix homeserver with `maubot` to bots developed with the [Talks Hippy](https://gitlab.com/gllona/talks-hippy) framework

## Features

- Bridges your Matrix server to bots developed with Talks Hippy
- Multiple Talks Hippy bots supported with a single `maubot` instance
- Support Matrix, WhatsApp, Signal and Telegram by using `mautrix` bridges
- Externalized configuration, no need to change the code
- Uses `asyncio` for efficient concurrency
- Operator can turn the bridge on and off per room. Use Element and log in as the Matrix bot user
- Bot echo messages can be forwarded to the Talks Hippy bot, optionally

## Prerequisites

Server:

1. A Matrix homeserver. You can deploy one using the Ansible playbook from https://github.com/spantaleev/matrix-docker-ansible-deploy
2. `maubot` installed in your Matrix homeserver
3. Custom configuration telling Synapse to allow the bots send many messages per second. You can set this configuration in
   the Ansible playbook `vars.yml` file:
```
matrix_synapse_rc_message:
  per_second: 100
  burst_count: 500
```

Local machine:

1. Python 3 with `pip` and `venv`

## Usage

1. Clone the repo from https://github.com/nightgreenwolf/talks-hippy-bridge
2. `bin/build_dependencies.sh` (run once)
3. `bin/zip_plugin.sh` (run for every deploy)
4. Upload `plugin.mbp` to maubot

## Configuration

The custom configuration is in `base-config.yaml`. You can specify a custom configuration in the `maubot` web admin tool.

Generally you will need to set only custom values for `matrix_bot_user`, `talks_server`, `talks_port`, `bot_on_regex`, `bot_off_regex` and
`room_tags`. It is recommended to set `forward_bot_messages` to `false` if you are not using bot interceptors in the
Talks Hippy bot.

```
# The Matrix user associated with the bot
matrix_bot_user : "@bot:example.com"

# The IP address or server name where the Talks Hippy bot is running
talks_server : "127.0.0.1"

# The HTTP port where the Talks Hippy bot is running
talks_port : 8080

# Tested with `http`
talks_protocol : "http"

# Talks endpoint path to send a message to Talks
talks_receive_message : "/matrix/receiveMessage"

# Talks endpoint path to query for messages to send back to users
talks_get_messages : "/matrix/getMessages"

# Talks endpoint path to confirm messages sent to users
talks_confirm_messages : "/matrix/confirmMessages"

# Talks endpoint path to tar rooms according to messages matching the `room_tags` values
talks_tag_room : "/matrix/tagRoom"

# If a message sent by the bot user matches this regex, the bridge is activated for the room 
bot_on_regex : '.*Continue.*'

# If a message sent by the bot user matches this regex, the bridge is deactivated for the room
bot_off_regex : '.*Hola!.*'

# If `true`, message actions sent by Talks Hippy will be displayed as numeric shortcuts
hints : true

# If `true`, all messages sent by the Matrix user will be forwarded to the Talks bot, including echo messages
forward_bot_messages : false

# List of regular expressions that trigger the `talks_tag_room` Talks endpoint. Each regular expression has a
# tag and a value sent to Talks. Optionally, a message specified by `trigger` will be fabricated by the Talks bot as if
# it were coming from the user
room_tags:
  - regex : '.*end user.*'
    tag : "profile"
    value : "end_user"
    trigger: "survey client-test-1"
  - regex : '.*service provider.*'
    tag : "profile"
    value : "service_provider"
    trigger: null

# The timeout, in msecs, for the `talks_receive_message` Talks endpoint
talks_receive_message_timeout: 1800

# Maximum size of the message deduplication cache
deduplication_cache_size : 1024

# Maximum size of the cache that filters out echo messages
echo_cache_size : 1024

# Talks Hippy HTTP calls timeout in seconds
fixed_timeout : 3.05

# The delay for each cycle of the message fetcher task, in seconds
message_fetcher_delay : 0.2

# The delay between two consecutive messages sent to the same room, in seconds
message_propagator_delay : 0.5

# The delay between the last message and the hints message, in seconds 
hints_delay : 1.0

# The Talks Hippy bot API key for HTTP calls
talks_api_key : "TALKS_API_KEY"
```

## Author

Copyright (C) 2023-2024 Night Green Wolf <mailto:nightgreenwolf@protonmail.com> and Gorka Llona <mailto:gllona@gmail.com>
