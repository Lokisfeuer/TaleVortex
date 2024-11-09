import openai
import time

client = openai.OpenAI()

'''
def call_openai(story, unit_type, description, feature_schema):
    # Prepare the prompt for OpenAI
    prompt = create_openai_prompt(story, unit_type, description, feature_schema)

    try:
        response = openai.ChatCompletion.create(
            model="gpt-4",
            messages=[
                {"role": "system",
                 "content": "You are an assistant that helps fill out feature values for units in a story based on a description."},
                {"role": "user", "content": prompt}
            ],
        )

        # Parse the assistant's reply
        assistant_reply = response['choices'][0]['message']['content']
        prefilled_features = json.loads(assistant_reply)

        return None, prefilled_features

    except Exception as e:
        errors = [f"Error generating features: {e}"]

        return errors, None
'''

def call_openai(messages, model="gpt-4o-mini"):
    t = time.time()
    response = openai.chat.completions.create(
        model=model,
        messages=messages
    )
    print(f'API request took {time.time() - t}')

    # Parse the assistant's reply
    assistant_reply = response.choices[0].message.content
    if assistant_reply.startswith('```json') and assistant_reply.endswith('```'):
        assistant_reply = assistant_reply[8:-4]

    return assistant_reply
