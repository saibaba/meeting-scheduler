import os

async def invoke_llm(llm, messages):
    print("LLM request =>")
    for m in messages:
        print(m)

    resp = await llm.ainvoke(messages)
    print(resp.content)
    return resp

