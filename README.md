## Purpose

Purpose is just to show basic example of langgraph, nothing fancy, no proper memory management or anything.

## Setup

Modify .venv and add your OPENAI_API_KEY.


## Testing

Run agent service in development mode:

```
$ fastapi dev
```

Above service could be invoked from curl client.


### With calendar slot available

Sample request:

```
$ curl -s http://localhost:8000/chat \
  -H 'content-type: application/json' \
  -d '{"session_id":"demo1","message":"Set up a 30 minute meeting with Alex Chen about Q1 planning 01/27/2026 6 pm"}' 
```

Note: Use a new session_id every time as the requests are stored in a map in code. It is needed for multi-turn example (shown in the section below).


Sample response:

```
{"session_id":"demo1","reply":"Booked: Q1 planning with Alex Chen at 2026-01-27T18:00:00-08:00 for 30 minutes.","state":{"last_user_message":"Set up a 30 minute meeting with Alex Chen about Q1 planning 01/27/2026 6 pm","last_agent_message":"Booked: Q1 planning with Alex Chen at 2026-01-27T18:00:00-08:00 for 30 minutes.","draft":{"attendee_full_name":"Alex Chen","subject":"Q1 planning","start_time_iso":"2026-01-27T18:00:00-08:00","duration_minutes":30,"timezone":"America/Los_Angeles"},"status":"booked","suggestions":[],"booked_event":{"id":"1","attendee_full_name":"Alex Chen","subject":"Q1 planning","start_time_iso":"2026-01-27T18:00:00-08:00","duration_minutes":30},"override":false}}
```


### With calendar slot busy, a two-turn example


If the meeting is with any person whose name contains jeff or mike, the first time request is made, calender shows busy and agent suggests alternate slots.

Sample request:

```
curl -s http://localhost:8000/chat \
  -H 'content-type: application/json' \
  -d '{"session_id":"demo2","message":"Set up a 30 minute meeting with Jeff Chen about Q1 planning 01/27/2026 6 pm"}' 
```

Response:

```
{"session_id":"demo2","reply":"Jeff Chen is busy then. How about: Wed, Jan 28 at 9:00 AM; Wed, Jan 28 at 9:30 AM; Wed, Jan 28 at 10:00 AM ?","state":{"last_user_message":"Set up a 30 minute meeting with Jeff Chen about Q1 planning 01/27/2026 6 pm","last_agent_message":"Jeff Chen is busy then. How about: Wed, Jan 28 at 9:00 AM; Wed, Jan 28 at 9:30 AM; Wed, Jan 28 at 10:00 AM ?","draft":{"attendee_full_name":"Jeff Chen","subject":"Q1 planning","start_time_iso":"2026-01-27T18:00:00-08:00","duration_minutes":30,"timezone":"America/Los_Angeles"},"status":"proposing_alternatives","suggestions":[{"start_time_iso":"2026-01-28T09:00:00-08:00","duration_minutes":30},{"start_time_iso":"2026-01-28T09:30:00-08:00","duration_minutes":30},{"start_time_iso":"2026-01-28T10:00:00-08:00","duration_minutes":30}],"override":true}}%     
```

Send a new message with one of the slots picked and it will be booked. 

Request:

```
$ curl -s http://localhost:8000/chat \
  -H 'content-type: application/json' \
  -d '{"session_id":"demo2","message":"2026-01-27T18:00:00-08:00 is great! book it!"}'                               
```

Response:

```
{"session_id":"demo2","reply":"Booked: Q1 planning with Jeff Chen at 2026-01-27T18:00:00-08:00 for 30 minutes.","state":{"last_user_message":"Set up a 30 minute meeting with Jeff Chen about Q1 planning 01/27/2026 6 pm","last_agent_message":"Booked: Q1 planning with Jeff Chen at 2026-01-27T18:00:00-08:00 for 30 minutes.","draft":{"attendee_full_name":"Jeff Chen","subject":"Q1 planning","start_time_iso":"2026-01-27T18:00:00-08:00","duration_minutes":30,"timezone":"America/Los_Angeles"},"status":"booked","suggestions":[],"booked_event":{"id":"1","attendee_full_name":"Jeff Chen","subject":"Q1 planning","start_time_iso":"2026-01-27T18:00:00-08:00","duration_minutes":30},"override":true}}
```

## Langchain workflow

![alt text](workflow_graph.png "Workflow")


## Building

```
$ python3 -m venv .venv
$ source .venv/bin/activate
$ pip install -e .
$ pip install build
$ python3 -m build
```

Now you can start python interpreter and import modules.

## workflow image

Running the following command generates the image:

```
$ python3 gen_graph.py
```

## TODO

1. Add proper memory management
2. Replace mock calander with real one
3. Add additional steps / nodes to handle missing or incomplete info
