import requests
import json

import threading
import asyncio
from websockets.sync.client import connect

SETTINGS = json.load(open("settings.json"))

API_URL = "https://api.iclicker.com"

COURSE_STATUS_ENDPOINT = API_URL + "/student/course/status"

JOIN_CLASS_ENDPOINT_TEMPLATE = API_URL + "/trogon/v2/course/attendance/join/{courseId}"

GET_PUSHER_CLUSTER_ENDPOINT = API_URL + "/v1/settings/pusher-cluster-primary/value"

PUSHER_ENDPOINT_TEMPLATE = "wss://ws-{cluster}.pusher.com/app/{clusterKey}?protocol=7&client=js&version=8.4.0&flash=false"

AUTHENTICATE_PUSHER_CHANNEL_ENDPOINT = API_URL + "/v1/websockets/authenticate-pusher-channel"

POST_ANSWER_ENDPOINT_TEMPLATE = API_URL + "/v2/activities/{activityId}/questions/{questionId}/user-questions/"
PUT_ANSWER_ENDPOINT_TEMPLATE = API_URL + "/v2/activities/{activityId}/questions/{questionId}/user-questions/{userQuestionId}"

GET_QUESTIONS_ENDPOINT_TEMPLATE = API_URL + "/v2/reporting/courses/{courseId}/activities/{activityId}/questions/view"

USER_ID = SETTINGS['userId']
COURSE_ID = SETTINGS['courseId']
AUTH_TOKEN = SETTINGS['authToken']

HEADERS = { "Authorization": AUTH_TOKEN }

ENDPOINT_TEMPLATE_OPTIONS = ['courseId', 'cluster', 'clusterKey', 'activityId', 'questionId', 'userQuestionId']


class StoppableThread(threading.Thread):
    """Thread class with a stop() method. The thread itself has to check
    regularly for the stopped() condition."""

    def __init__(self,  *args, **kwargs):
        super(StoppableThread, self).__init__(*args, **kwargs)
        self._stop_event = threading.Event()

    def stop(self):
        self._stop_event.set()

    def stopped(self):
        return self._stop_event.is_set()

class QuestionThread(StoppableThread):
    def __init__(self, course_id, activity_id, question_id):
        super().__init__()
        self.course_id = course_id
        self.activity_id = activity_id
        self.question_id = question_id

    def run(self):
        asyncio.run(self.handle_question(self.course_id, self.activity_id, self.question_id))

    async def handle_question(self, course_id, activity_id, question_id):
        """async function to handle a question. Continuously updates answer."""
        print("Answering question...")
        post_answer_endpoint = generate_endpoint(POST_ANSWER_ENDPOINT_TEMPLATE, options={ "activityId": activity_id, "questionId": question_id })
        post_json_data = { "answer": "a","userId": USER_ID, "activityId": activity_id, "questionId": question_id, "clientType": "WEB" }

        # answer automatically
        post_answer = requests.post(post_answer_endpoint, json=post_json_data, headers=HEADERS)
        print("answer question response: " + str(post_answer))
        if post_answer.status_code != 200:
            print("ERROR: answer_question status code is " + str(post_answer.status_code))
            return

        user_question_id = post_answer.json()['userQuestionId']
        put_answer_endpoint = generate_endpoint(PUT_ANSWER_ENDPOINT_TEMPLATE, options={ "activityId": activity_id, "questionId": question_id, "userQuestionId": user_question_id })
        put_json_data = { "answer": "a","userId": USER_ID, "activityId": activity_id, "userQuestionId": user_question_id }

        while not self.stopped():
            await asyncio.sleep(1)
            if self.stopped():
                break

            # get most popular answer
            get_questions = requests.get(generate_endpoint(GET_QUESTIONS_ENDPOINT_TEMPLATE, options={ "courseId": course_id, "activityId": activity_id }), headers=HEADERS)
            print("get questions response: " + str(get_questions))
            question_answers = get_questions.json()['questions'][-1]['answerOverview']

            best_answer = question_answers[0];
            for question in question_answers:
                if question['count'] > best_answer['count']:
                    best_answer = question

            if (put_json_data['answer'] == best_answer['answer'].lower()):
                continue

            put_json_data['answer'] = best_answer['answer'].lower()

            put_answer = requests.put(put_answer_endpoint, json=put_json_data, headers=HEADERS)
            print("change answer response: " + str(put_answer))

def generate_endpoint(template_endpoint: str, options={}) -> str:
    """generates and endpoint from a given template"""
    res = template_endpoint
    for option in ENDPOINT_TEMPLATE_OPTIONS:
        if "{" + option + "}" in res:
            if not option in options:
                raise ValueError(option + " not provided to endpoint template " + template_endpoint)
            else:
                res = res.replace("{" + option + "}", options[option])

    if "{" in res or "}" in res:
        print("WARNING: url endpoint result " + str(res) + "contains '{' or '}'. Is there a template error?")

    return res

def load_json_string(json_string):
    """helper for loading json strings from websocket connection"""
    res = json.loads(json_string);
    if 'data' in res:
        res['data'] = json.loads(res['data'])
    return res;

def join_class(course_id):
    """attempts to join the class with the given course_id"""
    print("Attempting to join course...")
    join_course = requests.post(generate_endpoint(JOIN_CLASS_ENDPOINT_TEMPLATE, options={ "courseId": course_id}), headers=HEADERS)
    print("join course response: " + str(join_course))

async def main(course_id):
    # try joining at the start just in case class has already started.
    join_class(course_id);

    pusher_cluster = requests.get(GET_PUSHER_CLUSTER_ENDPOINT, headers=HEADERS)
    print("pusher cluster response: " + str(pusher_cluster))
    cluster = pusher_cluster.json()["cluster"]
    cluster_key = pusher_cluster.json()["key"]

    with connect(generate_endpoint(PUSHER_ENDPOINT_TEMPLATE, options={ "cluster": cluster, "clusterKey": cluster_key })) as websocket:
        connect_message = load_json_string(websocket.recv())
        # print(json.dumps(connect_message, indent=4))

        # authenticate pusher channel
        pusher_channel = requests.post(AUTHENTICATE_PUSHER_CHANNEL_ENDPOINT, data={"socket_id": connect_message['data']['socket_id'], "channel_name": f"private-{course_id}" }, headers=HEADERS)
        print(f"pusher channel response: {pusher_channel}")
        channel_auth_token = pusher_channel.json()['auth']

        websocket.send(f'{{"event":"pusher:subscribe","data":{{"auth":"{channel_auth_token}","channel":"private-{course_id}"}}}}')

        question_handler_thread = None

        while True:
            msg = load_json_string(websocket.recv());
            print(json.dumps(msg, indent=4))

            match(msg['event']):
                case "ATTENDANCE_STARTED":
                    print("-- ATTENDANCE STARTED --")
                    join_class(course_id)
                case "question":
                    print("-- QUESTION STARTED --")
                    activity_id = msg['data']['activityId']
                    question_id = msg['data']['questionId']
                    # question_handler_thread = StoppableThread(target=asyncio.run, args=(handle_question(activity_id, question_id),))
                    question_handler_thread = QuestionThread(course_id, activity_id, question_id)

                    question_handler_thread.start()
                case "endQuestion":
                    print("-- QUESTION ENDED --")
                    if question_handler_thread != None:
                        question_handler_thread.stop()
                        question_handler_thread = None
                case "MEETING_ENDED":
                    print("-- MEETING ENDED --")
                    if question_handler_thread != None:
                        question_handler_thread.stop()
                        question_handler_thread = None

asyncio.run(main(COURSE_ID))
