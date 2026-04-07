import requests
import json

import threading
import asyncio
from requests.api import get
from websockets.sync.client import connect

SETTINGS = json.load(open("settings.json"))

API_URL = "https://api.iclicker.com"

PROFILE_ENDPOINT = API_URL + "/trogon/v4/profile"
COURSES_ENDPOINT = API_URL + "/v1/users/{userId}/views/student-courses"
CLASS_SECTIONS_ENDPOINT = API_URL + "/v2/courses/{courseId}/class-sections?recordsPerPage=1&pageNumber=1&expandChild=activities&expandChild=userActivities&expandChild=attendances&expandChild=questions&expandChild=userQuestions&expandChild=questionGroups&userId={userId}"

COURSE_STATUS_ENDPOINT = API_URL + "/student/course/status"

JOIN_CLASS_ENDPOINT_TEMPLATE = API_URL + "/trogon/v2/course/attendance/join/{courseId}"
JOIN_CLASS_V1_ENDPOINT_TEMPLATE = API_URL + "/v1/meetings/{meetingId}/join-participant"

GET_PUSHER_CLUSTER_ENDPOINT = API_URL + "/v1/settings/pusher-cluster-primary/value"

PUSHER_ENDPOINT_TEMPLATE = "wss://ws-{cluster}.pusher.com/app/{clusterKey}?protocol=7&client=js&version=8.4.0&flash=false"

AUTHENTICATE_PUSHER_CHANNEL_ENDPOINT = API_URL + "/v1/websockets/authenticate-pusher-channel"

POST_ANSWER_ENDPOINT_TEMPLATE = API_URL + "/v2/activities/{activityId}/questions/{questionId}/user-questions/"
PUT_ANSWER_ENDPOINT_TEMPLATE = API_URL + "/v2/activities/{activityId}/questions/{questionId}/user-questions/{userQuestionId}"

GET_QUESTIONS_ENDPOINT_TEMPLATE = API_URL + "/v2/reporting/courses/{courseId}/activities/{activityId}/questions/view"

AUTH_TOKEN = SETTINGS['authToken']
# initially unset. Get's pulled from server
USER_ID = None

HEADERS = { "Authorization": f"Bearer {AUTH_TOKEN}" }

ENDPOINT_TEMPLATE_OPTIONS = ['courseId', 'cluster', 'clusterKey', 'activityId', 'questionId', 'userQuestionId', 'userId', 'meetingId' ]

COURSE_ID_TO_ENROLLMENT_ID = {}

UPDATE_ANSWER_TIME_SECONDS = 2
VALID_ANSWER_PERCENTAGE_THRESHOLD = 60

def format_print(course_id, message):
    print(f"[{course_id[:5]}] {message}")

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

ANSWER_TYPE_TO_KEY = { "SINGLE_ANSWER": "answer", "MULTIPLE_ANSWER": "answers" }

def add_default_answer(data, answer_key):
    if answer_key == "answer":
        data[answer_key] = "A"
    if answer_key == "answers":
        data[answer_key] = ["A"]

    return data

class QuestionThread(StoppableThread):
    def __init__(self, course_id, activity_id, question_id, answer_type="SINGLE_ANSWER"):
        super().__init__()
        self.course_id = course_id
        self.activity_id = activity_id
        self.question_id = question_id
        self.answer_type = answer_type

    def run(self):
        asyncio.run(self.handle_question(self.course_id, self.activity_id, self.question_id, self.answer_type))

    async def handle_question(self, course_id, activity_id, question_id, answer_type):
        """async function to handle a question. Continuously updates answer."""
        format_print(course_id, "Answering question...")

        post_answer_endpoint = generate_endpoint(POST_ANSWER_ENDPOINT_TEMPLATE, options={ "activityId": activity_id, "questionId": question_id })
        post_json_data = { "userId": USER_ID, "activityId": activity_id, "questionId": question_id, "clientType": "WEB" }

        # handle different requests for different question types
        answer_key = ANSWER_TYPE_TO_KEY.get(answer_type)
        if answer_key == None:
            format_print(course_id, "invalid or unanswerable answer type")
            return

        post_json_data = add_default_answer(post_json_data, answer_key)

        # answer automatically
        post_answer = requests.post(post_answer_endpoint, json=post_json_data, headers=HEADERS)
        if post_answer.status_code != 200:
            if post_answer.status_code == 409 and "UserQuestion already exists." == post_answer.json()['message']:
                format_print(course_id, "Question has already been answered.")
            else:
                format_print(course_id, "ERROR: answer_question status code is " + str(post_answer.status_code))
            format_print(course_id, "Stopping question handler...")
            return
        else:
            format_print(course_id, "Successfully answered question.")

        user_question_id = post_answer.json()['userQuestionId']
        put_answer_endpoint = generate_endpoint(PUT_ANSWER_ENDPOINT_TEMPLATE, options={ "activityId": activity_id, "questionId": question_id, "userQuestionId": user_question_id })
        put_json_data = { "userId": USER_ID, "activityId": activity_id, "userQuestionId": user_question_id }
        put_json_data = add_default_answer(put_json_data, answer_key)

        while not self.stopped():
            await asyncio.sleep(UPDATE_ANSWER_TIME_SECONDS)
            if self.stopped():
                break

            # get most popular answer
            get_questions = requests.get(generate_endpoint(GET_QUESTIONS_ENDPOINT_TEMPLATE, options={ "courseId": course_id, "activityId": activity_id }), headers=HEADERS)
            format_print(course_id, "get questions response: " + str(get_questions))
            question_answers = get_questions.json()['questions'][-1]['answerOverview']

            best_answer = question_answers[0];
            valid_answers = [];

            for answer in question_answers:
                if answer['count'] > best_answer['count']:
                    best_answer = answer
                if answer['percentageOfTotalResponses'] > VALID_ANSWER_PERCENTAGE_THRESHOLD:
                    valid_answers.append(answer['answer'])

            if (answer_key == "answer"):
                if (put_json_data['answer'] == best_answer['answer']):
                    continue

                format_print(course_id, f"changing answer to {best_answer['answer']}...")
                put_json_data[answer_key] = best_answer['answer']

            elif (answer_key == "answers"):
                if (put_json_data['answers'] == valid_answers):
                    continue

                format_print(course_id, f"changing answer to {valid_answers}...")
                put_json_data[answer_key] = valid_answers


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

def join_class(course_id) -> bool:
    """attempts to join the class with the given course_id"""
    format_print(course_id, f"Attempting to join course ({course_id[:5]})...")
    post_join = requests.post(generate_endpoint(JOIN_CLASS_ENDPOINT_TEMPLATE, options={ "courseId": course_id}),json={ "id": course_id, "geo": {"lat":1.0,"lon":1.0}}, headers=HEADERS)

    is_present = post_join.status_code == 200 and 'result' in post_join.json() and post_join.json()['result'] == "PRESENT"
    is_no_class = post_join.status_code == 409 and "There is no active Attendance." in post_join.json()['error']['desc']
    is_location_error = post_join.status_code == 200 and 'method' in post_join.json() and post_join.json()['method'] == "GEO"

    if is_present:
        format_print(course_id, "Successfully joined class.")
        return True
    elif is_no_class:
        format_print(course_id, "No active attendance found, did not join.")
        # since classes that use v1 of the api, it will return as if there is no active attendance.
        return join_class_v1(course_id);
    elif not is_location_error:
        format_print(course_id, "ERROR: join course failed with response: " + str(post_join.content))

    if not is_location_error:
        return False

    # resolve location error
    format_print(course_id, "Class requires location. Attempting to join again...")
    post_join = requests.post(generate_endpoint(JOIN_CLASS_ENDPOINT_TEMPLATE, options={ "courseId": course_id}),json={ "id": course_id, "geo": post_join.json()['instructorLocation'] }, headers=HEADERS)

    if post_join.status_code == 200 and 'result' in post_join.json() and post_join.json()['result'] == "PRESENT":
        format_print(course_id, "Successfully spoofed location and joined class.")
        return True
    else:
        format_print(course_id, f"ERROR: join course failed with response: {post_join.content}")

    return False

def join_class_v1(course_id) -> bool:
    format_print(course_id, f"(v1) Attempting to join course ({course_id[:5]}) with  v1 api...")

    get_course_status = requests.post(COURSE_STATUS_ENDPOINT, json={"courseId": course_id}, headers=HEADERS)
    meeting_id = get_course_status.json()['meetingId']
    if meeting_id == None:
        format_print(course_id, "(v1) No active attendance found, did not join.")
        return False

    post_join_v1 = requests.post(generate_endpoint(JOIN_CLASS_V1_ENDPOINT_TEMPLATE, options={ 'meetingId': meeting_id }), json={ "meetingId": meeting_id, "enrollmentId": COURSE_ID_TO_ENROLLMENT_ID[course_id] }, headers=HEADERS)

    if post_join_v1.status_code == 200 and 'joined' in post_join_v1.json() and post_join_v1.json()['joined'] != None:
        format_print(course_id, "(v1) Successfully joined class.")
        return True
    else:
        format_print(course_id, f"ERROR: (v1) join course failed with code {post_join_v1.status_code} and content {post_join_v1.content}")
    return False


async def handle_course(course_id):
    # try joining at the start just in case class has already started.
    join_class_successful = join_class(course_id);

    get_pusher_cluster = requests.get(GET_PUSHER_CLUSTER_ENDPOINT, headers=HEADERS)
    format_print(course_id, "get pusher cluster response: " + str(get_pusher_cluster))
    cluster = get_pusher_cluster.json()["cluster"]
    cluster_key = get_pusher_cluster.json()["key"]

    format_print(course_id, "Connecting to websocket...")
    with connect(generate_endpoint(PUSHER_ENDPOINT_TEMPLATE, options={ "cluster": cluster, "clusterKey": cluster_key })) as websocket:
        connect_message = load_json_string(websocket.recv())
        # print(json.dumps(connect_message, indent=4))

        # authenticate pusher channel
        pusher_channel = requests.post(AUTHENTICATE_PUSHER_CHANNEL_ENDPOINT, data={"socket_id": connect_message['data']['socket_id'], "channel_name": f"private-{course_id}" }, headers=HEADERS)

        if pusher_channel.status_code == 200:
            format_print(course_id, "Successfully connected to pusher channel")
        else:
            format_print(course_id, f"ERROR: unexpected pusher_channel response: {pusher_channel}")

        channel_auth_token = pusher_channel.json()['auth']

        websocket.send(f'{{"event":"pusher:subscribe","data":{{"auth":"{channel_auth_token}","channel":"private-{course_id}"}}}}')

        question_handler_thread = None
 
        # attempt to join current activity if the class is already active.
        if join_class_successful:
            get_classes = requests.get(generate_endpoint(CLASS_SECTIONS_ENDPOINT, options={ "courseId": course_id, "userId": USER_ID }), headers=HEADERS)
            most_recent_activities = get_classes.json()[0]['activities']
            if len(most_recent_activities) > 0:
                most_recent_question = most_recent_activities[-1]['questions'][-1]

                question_handler_thread = QuestionThread(course_id, most_recent_question['activityId'], most_recent_question['_id'], most_recent_question['answerType'])
                question_handler_thread.start()


        while True:
            msg = load_json_string(websocket.recv());
            # print(json.dumps(msg, indent=4))

            if msg['event'] == "ATTENDANCE_STARTED":
                format_print(course_id, "-- ATTENDANCE STARTED --")
                join_class(course_id)
            if msg['event'] ==  "question":
                format_print(course_id, "-- QUESTION STARTED --")
                activity_id = msg['data']['activityId']
                question_id = msg['data']['questionId']
                answer_type = msg['data']['answerType']
                # question_handler_thread = StoppableThread(target=asyncio.run, args=(handle_question(activity_id, question_id),))
                question_handler_thread = QuestionThread(course_id, activity_id, question_id, answer_type)
                question_handler_thread.start()
            if msg['event'] == "endQuestion":
                format_print(course_id, "-- QUESTION ENDED --")
                if question_handler_thread != None:
                    question_handler_thread.stop()
                    question_handler_thread = None
            if msg['event'] == "MEETING_ENDED":
                format_print(course_id, "-- MEETING ENDED --")
                if question_handler_thread != None:
                    question_handler_thread.stop()
                    question_handler_thread = None

async def main():
    globals()['USER_ID'] = requests.get(PROFILE_ENDPOINT, headers=HEADERS).json()['userid']
    print(f"USER_ID is {USER_ID}")

    get_courses = requests.get(generate_endpoint(COURSES_ENDPOINT, { "userId": USER_ID }), headers=HEADERS)
    
    course_ids = []
    for enrollment in get_courses.json()['enrollments']:
        if enrollment['archived'] == None:
            course_ids.append(enrollment['courseId'])
            globals()['COURSE_ID_TO_ENROLLMENT_ID'][enrollment['courseId']] = enrollment['enrollmentId']
    print(f"course ids: {course_ids}")

    for course_id in course_ids:
        thread = threading.Thread(target=asyncio.run, args=(handle_course(course_id),))
        thread.start()
    # await handle_course(COURSE_ID)

if AUTH_TOKEN == "your token here":
    print("Copy a valid auth token into settings.json")
else:
    asyncio.run(main())
