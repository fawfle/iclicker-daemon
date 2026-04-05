import requests
import json

import asyncio
from websockets.sync.client import connect

SETTINGS = json.load(open("settings.json"))

API_URL = "https://api.iclicker.com"

COURSE_STATUS_ENDPOINT = API_URL + "/student/course/status"

JOIN_CLASS_ENDPOINT_TEMPLATE = API_URL + "/trogon/v2/course/attendance/join/{courseId}"

GET_PUSHER_CLUSTER_ENDPOINT = API_URL + "/v1/settings/pusher-cluster-primary/value"

PUSHER_ENDPOINT_TEMPLATE = "wss://ws-{cluster}.pusher.com/app/{clusterKey}?protocol=7&client=js&version=8.4.0&flash=false"

AUTHENTICATE_PUSHER_CHANNEL_ENDPOINT = API_URL + "/v1/websockets/authenticate-pusher-channel"

ANSWER_QUESTION_ENDPOINT_TEMPLATE = API_URL + "/v2/activities/{activityId}/questions/{questionId}/user-questions/"

USER_ID = SETTINGS['userId']
COURSE_ID = SETTINGS['courseId']
AUTH_TOKEN = SETTINGS['authToken']

HEADERS = { "Authorization": AUTH_TOKEN }

ENDPOINT_TEMPLATE_OPTIONS = ['courseId', 'cluster', 'clusterKey', 'activityId', 'questionId']

def generate_endpoint(template_endpoint: str, options={}) -> str:
    """generates and endpoint from a given template"""
    res = template_endpoint
    for option in ENDPOINT_TEMPLATE_OPTIONS:
        if "{" + option + "}" in res:
            if not option in options:
                raise ValueError(option + " not provided to endpoint template " + template_endpoint)
            else:
                res = res.replace("{" + option + "}", options[option])

    return res

def load_json_string(json_string):
    """helper for loading json strings from websocket connection"""
    res = json.loads(json_string);
    if 'data' in res:
        res['data'] = json.loads(res['data'])
    return res;

def join_class(course_id):
    """attempts to join the class with the given course_id"""
    join_course = requests.post(generate_endpoint(JOIN_CLASS_ENDPOINT_TEMPLATE, options={ "courseId": course_id}), headers=HEADERS)
    print("join course response: " + str(join_course))

async def handle_question(activity_id, question_id):
    """async function to handle a question. Continuously updates answer."""
    answer_question = requests.post(generate_endpoint(ANSWER_QUESTION_ENDPOINT_TEMPLATE, options={ "activityId": activity_id, "questionId": question_id }), json=({ "answer": "a","userId": USER_ID, "activityId": activity_id, "questionId": question_id, "clientType": "WEB" }), headers=HEADERS)
    print("answer question response: " + str(answer_question))


async def main():
    # course_status = requests.post(COURSE_STATUS_ENDPOINT, None, { "courseId": COURSE_ID }, headers=HEADERS)
    # if course_status.status_code != 200:
    #     print(f"ERROR: course_status returned with status code {course_status.status_code}. Exiting...")
    #     return
    #
    # meeting_id = course_status.json()['meetingId']
    #
    # if meeting_id is None:
    #     print("ERROR: no meeting id found. Class is most likely not active.")
    #     return
    # print("meeting id: " + str(meeting_id))
    
    # try joining at the start just in case class has already started.
    join_class(COURSE_ID);

    pusher_cluster = requests.get(GET_PUSHER_CLUSTER_ENDPOINT, headers=HEADERS)
    print("pusher cluster response: " + str(pusher_cluster))
    cluster = pusher_cluster.json()["cluster"]
    cluster_key = pusher_cluster.json()["key"]

    with connect(generate_endpoint(PUSHER_ENDPOINT_TEMPLATE, options={ "cluster": cluster, "clusterKey": cluster_key })) as websocket:
        connect_message = load_json_string(websocket.recv())
        print(json.dumps(connect_message, indent=4))

        # authenticate pusher channel
        pusher_channel = requests.post(AUTHENTICATE_PUSHER_CHANNEL_ENDPOINT, data={"socket_id": connect_message['data']['socket_id'], "channel_name": f"private-{COURSE_ID}" }, headers=HEADERS)
        print(f"pusher channel response: {pusher_channel}")
        channel_auth_token = pusher_channel.json()['auth']

        websocket.send(f'{{"event":"pusher:subscribe","data":{{"auth":"{channel_auth_token}","channel":"private-{COURSE_ID}"}}}}')
        while True:
            msg = load_json_string(websocket.recv());
            print(json.dumps(msg, indent=4))

            match(msg['event']):
                case "ATTENDANCE_STARTED":
                    join_class(COURSE_ID)
                case "question":
                    activity_id = msg['data']['activityId']
                    question_id = msg['data']['questionId']
                    await handle_question(activity_id, question_id)


asyncio.run(main())
