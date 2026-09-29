from openai import OpenAI
import os

# Initialize OpenAI client
client = OpenAI(
    api_key = os.getenv(""DASHSCOPE_API_KEY""),
    base_url=""https://dashscope-intl.aliyuncs.com/compatible-mode/v1""
)

reasoning_content = """"  # Define complete reasoning process
answer_content = """"     # Define complete response
is_answering = False    # Determine if reasoning has ended and response has started
enable_thinking = False

# Create chat completion request
completion = client.chat.completions.create(
    model=""qwen3-vl-flash"",
    messages=[
        {
            ""role"": ""user"",
            ""content"": [
                {
                    ""type"": ""image_url"",
                    ""image_url"": {
                        ""url"": ""https://img.alicdn.com/imgextra/i1/O1CN01gDEY8M1W114Hi3XcN_!!6000000002727-0-tps-1024-406.jpg""
                    },
                },
                {""type"": ""text"", ""text"": ""How to solve this problem?""},
            ],
        },
    ],
    stream=True,
    # enable_thinking parameter enables reasoning; thinking_budget sets the maximum token count for reasoning
    # qwen-vl-plus, qwen3-vl-plus-2025-09-23 can use enable_thinking to turn reasoning on/off; qwen3-vl-235b-a22b-thinking supports enable_thinking, other Qwen-VL models do not support it
    extra_body={
        'enable_thinking': True,
        ""thinking_budget"": 500},

    # Uncomment below to return token usage in the final chunk
    # stream_options={
    #     ""include_usage"": True
    # }
)

if enable_thinking:
    print(""\n"" + ""="" * 20 + ""Reasoning Process"" + ""="" * 20 + ""\n"")

for chunk in completion:
    # If chunk.choices is empty, print usage
    if not chunk.choices:
        print(""\nUsage:"")
        print(chunk.usage)
    else:
        delta = chunk.choices[0].delta
        # Print reasoning process
        if hasattr(delta, 'reasoning_content') and delta.reasoning_content != None:
            print(delta.reasoning_content, end='', flush=True)
            reasoning_content += delta.reasoning_content
        else:
            # Start response
            if delta.content != """" and is_answering is False:
                print(""\n"" + ""="" * 20 + ""Complete Response"" + ""="" * 20 + ""\n"")
                is_answering = True
            # Print response process
            print(delta.content, end='', flush=True)
            answer_content += delta.content

# print(""="" * 20 + ""Complete Reasoning Process"" + ""="" * 20 + ""\n"")
# print(reasoning_content)
# print(""="" * 20 + ""Complete Response"" + ""="" * 20 + ""\n"")
# print(answer_content)