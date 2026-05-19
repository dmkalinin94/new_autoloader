# -*- coding: utf-8 -*-

# Jira
JIRA_TOKEN = "Basic <token>"
JIRA_SERVICE_URL = "https://hd.samoletgroup.ru/rest/assets/1.0/object/{}/attributes"
JIRA_CREATE_INC_URL = "https://hd-dev02.samoletgroup.ru/rest/api/2/issue"
JIRA_ISSUE_STATUS_URL = "https://hd-dev02.samoletgroup.ru/rest/api/2/issue/{}?fields=status"
JIRA_ISSUE_BROWSE_URL = "https://hd-dev02.samoletgroup.ru/browse/{}"

# Jira transition after KTalk thread creation
JIRA_THREAD_LINK_TRANSITION_ENABLED = False
JIRA_THREAD_LINK_TRANSITION_URL = "https://hd.samoletgroup.ru/rest/api/2/issue/{}/transitions"
JIRA_THREAD_LINK_TRANSITION_ID = "541"
JIRA_THREAD_LINK_CUSTOM_FIELD = "customfield_41700"
# False: log Jira transition errors and continue the new-incident flow.
# True: raise Jira HTTP errors and stop the flow after a failed transition.
JIRA_THREAD_LINK_TRANSITION_STRICT = False

# KTalk Bot API
KTALK_BASE_URL = "https://chat.ktalk.ru"
KTALK_BOT_USER = "zabbix_bot"
KTALK_JWT_TOKEN = "<ktalk_bot_jwt_token>"
KTALK_ROOM_ID = "!SWMeGogRrRLJIxnikt:matrix-9.ktalk.ru"
KTALK_REQUEST_RETRIES = 3
KTALK_RETRY_DELAY_SECONDS = 5

# KTalk web thread link
KTALK_THREAD_LINK_REQUIRED_PREFIX = "https://samoletgroup.ktalk.ru/app/messenger/"
KTALK_THREAD_WEB_URL_TEMPLATE = (
    "https://samoletgroup.ktalk.ru/app/messenger/{room_id}?thread_id={thread_root_event_id}"
)

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
