# -*- coding: utf-8 -*-

JIRA_TOKEN = "Bearer <token>"
JIRA_SERVICE_URL = "https://hd.samoletgroup.ru/rest/assets/1.0/object/{}/attributes"
JIRA_CREATE_INC_URL = "https://hd-dev02.samoletgroup.ru/rest/api/2/issue"
JIRA_ISSUE_STATUS_URL = "https://hd-dev02.samoletgroup.ru/rest/api/2/issue/{}?fields=status"
JIRA_ISSUE_BROWSE_URL = "https://hd-dev02.samoletgroup.ru/browse/{}"

KTALK_BASE_URL = "https://chat.ktalk.ru"
KTALK_BOT_USER = "zabbix_bot"
KTALK_JWT_TOKEN = "<ktalk_bot_jwt_token>"
KTALK_ROOM_ID = "!SWMeGogRrRLJIxnikt:matrix-9.ktalk.ru"
KTALK_HOST = "chat.ktalk.ru"
KTALK_TALK_HOST = "https://samoletgroup.ktalk.ru"
KTALK_BEARER_TOKEN = "Bearer <ktalk_bearer_token>"
KTALK_SEND_RETRIES = 3
KTALK_SEND_RETRY_DELAY_SEC = 1.5

RECIPIENT_RESOLVER_URL = "http://127.0.0.1:8000/resolve"

REQUEST_TIMEOUT = 30
VERIFY_SSL = False
LOG_FILE = "/tmp/autoalerter.log"

MANDATORY_RECIPIENTS = (
    "dm.kalinin",
    "d.boyarchuk",
    "t.sukhorukikh",
    "dk.korolev",
)
