# -*- coding: utf-8 -*-

# Jira
JIRA_TOKEN = "Bearer <token>"
JIRA_SERVICE_URL = "https://hd.samoletgroup.ru/rest/assets/1.0/object/{}/attributes"
JIRA_CREATE_INC_URL = "https://hd-dev02.samoletgroup.ru/rest/api/2/issue"
JIRA_ISSUE_STATUS_URL = "https://hd-dev02.samoletgroup.ru/rest/api/2/issue/{}?fields=status"
JIRA_ISSUE_BROWSE_URL = "https://hd-dev02.samoletgroup.ru/browse/{}"

# KTalk Bot API
KTALK_BASE_URL = "https://chat.ktalk.ru"
KTALK_BOT_USER = "zabbix_bot"
KTALK_JWT_TOKEN = "<ktalk_bot_jwt_token>"
KTALK_ROOM_ID = "!SWMeGogRrRLJIxnikt:matrix-9.ktalk.ru"
KTALK_REQUEST_RETRIES = 3
KTALK_RETRY_DELAY_SECONDS = 5

# KTalk Bearer API
KTALK_HOST = "chat.ktalk.ru"
KTALK_TALK_HOST = "https://samoletgroup.ktalk.ru"
KTALK_BEARER_TOKEN = "Bearer <ktalk_bearer_token>"

# External resolver API
RECIPIENT_RESOLVER_URL = "http://127.0.0.1:8000/resolve"

# Common runtime
REQUEST_TIMEOUT = 30
VERIFY_SSL = False
LOG_FILE = "/tmp/autoalerter.log"

# Invite behavior
KTALK_INVITES_DRY_RUN = False

# Flap-reopen behavior
FLAP_REOPEN_WINDOW_ENABLED = True
FLAP_REOPEN_APPLY_NIGHT_WINDOW = True
FLAP_REOPEN_TIME_START_HOUR = 21
FLAP_REOPEN_TIME_END_HOUR = 9
# Python datetime.weekday(): 5=Saturday, 6=Sunday
FLAP_REOPEN_WEEKDAYS = (5, 6)
FLAP_REOPEN_HOURS = 3
TIMEZONE_NAME = "Europe/Moscow"

# Mandatory recipients
MANDATORY_RECIPIENTS = (
    "dm.kalinin",
    "d.boyarchuk",
    "t.sukhorukikh",
    "dk.korolev",
)
