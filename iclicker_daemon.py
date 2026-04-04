import requests
import json

SETTINGS = json.load(open("settings.json"))

API_URL = "https://api.iclicker.com"

COURSE_STATUS_ENDPOINT = API_URL + "/student/course/status"

JOIN_COURSE_ENDPOINT_TEMPLATE = API_URL + "/trogon/v2/course/attendance/join/{courseId}"

COURSE_ID = SETTINGS['courseId']
AUTH_TOKEN = SETTINGS['authToken']

HEADERS = { "Authorization": AUTH_TOKEN }

def generate_endpoint(endpoint: str, course_id=None) -> str:
    res = endpoint
    if not course_id is None:
        res = res.replace("{courseId}", course_id)

    print(res)
    return res

def main():
    course_status = requests.post(COURSE_STATUS_ENDPOINT, None, { "courseId": COURSE_ID }, headers=HEADERS)

    meeting_id = course_status.json()['meetingId']

    if meeting_id is None:
        print("no meeting id found")
        return
    print("meeting id: " + str(meeting_id))

    join_course = requests.post(generate_endpoint(JOIN_COURSE_ENDPOINT_TEMPLATE, course_id=COURSE_ID), None, { "id": meeting_id }, headers=HEADERS)
    print("join course response: " + str(join_course))

main();
