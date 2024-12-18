import aiohttp
from typing import List, Tuple
import json
import jinja2
import re
import asyncio

CONTEXT_LENGTH = 10_000


async def test_prompt_with_model(url: str, prompt: str, model: str) -> str:
    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(
                url,
                json={
                    "prompt": prompt,
                    "model": model,
                    "stream": False,
                    "options": {"num_ctx": CONTEXT_LENGTH, "temperature": 0.1, "top_p": 0.7, "top_k": 20},
                },
            ) as response:
                result = await response.text()
                response = json.loads(result)
                return response["response"]
        except Exception as e:
            return f"Error: {str(e)}"


async def test_multiple_models(urls: List[str], prompt: str, models: List[str]) -> dict:
    tasks = [
        test_prompt_with_model(url, prompt, model) for url, model in zip(urls, models)
    ]
    results = await asyncio.gather(*tasks)
    return dict(zip(models, results))


async def test_prompt_with_chat_model(
    url: str, messages: List[dict], model: str
) -> dict:
    """
    Send a chat request to the API endpoint.

    Args:
        url: The API endpoint URL (will replace /generate with /chat)
        messages: List of message dictionaries with 'role' and 'content' keys
        model: The model identifier to use

    Returns:
        The model's response text
    """
    chat_url = url.replace("/generate", "/chat")

    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(
                chat_url,
                json={
                    "messages": messages,
                    "model": model,
                    "stream": False,
                    "options": {"num_ctx": CONTEXT_LENGTH, "temperature": 0.3, "top_p": 0.9, "top_k": 40},
                },
            ) as response:
                result = await response.text()
                response = json.loads(result)
                return response["message"]
        except Exception as e:
            return f"Error: {str(e)}"


def get_template_variables(template_str: str) -> List[str]:
    """Extract variable names from a Jinja2 template string"""
    env = jinja2.Environment()
    ast = env.parse(template_str)
    variables = set()

    def visit_node(node):
        if isinstance(node, jinja2.nodes.Name):
            variables.add(node.name)
        for child in node.iter_child_nodes():
            visit_node(child)

    visit_node(ast)
    return list(variables)


def validate_variables_with_template(
    values: dict, template_str: str
) -> Tuple[bool, str]:
    if isinstance(values, str):
        values = json.loads(values)
    template_vars = get_template_variables(template_str)
    for template_var in template_vars:
        if (
            template_var not in values.keys()
            or values[template_var] == ""
            or values[template_var] is None
        ):
            return False, template_var
    return True, ""


def evaluate_test_case(llm_output: str, expected_output: str) -> bool:
    return llm_output.strip().lower() == expected_output.strip().lower()


def _extract_judge_score(answer: str, split_str: str = "Total rating:") -> int:
    try:
        if split_str in answer:
            rating = answer.split(split_str)[1]
        else:
            rating = answer
        digit_groups = [el.strip() for el in re.findall(r"\d+(?:\.\d+)?", rating)]
        score = float(digit_groups[0])
        # Map to a float between 0 and 1
        if score == 0.0:
            return score
        return max(0.0, min(1.0, score / 4.0))
    except Exception as e:
        print(e)
        return 0


async def compare_strings_with_llm_judge(
    llm_output: str,
    expected_output: str,
    original_instruction: str,
    test_prompt_func,
    url: str,
    model: str,
) -> float:
    judge_prompt = f"""
You will be given a user_instruction, a system_answer and a expected_answer.
Your task is to provide a 'total rating' scoring how well the system_answer answers the user instruction expressed in the user_instruction and how well the system_answer matches the expected_answer.
Give your answer on a scale of 1 to 4, where 1 means that the system_answer is not helpful at all and does not match with the expected_answer, and 4 means that the system_answer completely and helpfully addresses the user_question and perfectly matches the expected_answer.

Here is the scale you should use to build your answer:
1: The system_answer is terrible: completely irrelevant to the user_instruction, or very partial. The system_answer is completely incorrect and does not match with the expected_answer.
2: The system_answer is mostly not helpful: misses some key aspects of the user_instruction. The system_answer has major differences or missing key elements compared to the expected_answer.
3: The system_answer is mostly helpful: provides support, but still could be improved. The system_answer has minor differences but maintains the core meaning compared to the expected_answer.
4: The system_answer is excellent: relevant, direct, detailed, and addresses all the concerns raised in the user_instruction. Perfect match of system_answer and expected_answer.

Provide your feedback as follows:

Feedback:::
Evaluation: (your rationale for the rating, as a text)
Total rating: (your rating, as a number between 1 and 4)

You MUST provide values for 'Evaluation:' and 'Total rating:' in your answer.

Now here are the user_instruction, system_answer and exptected_answer.

user_instruction: {original_instruction}
system_answer: {llm_output}
expected_answer: {expected_output}

Provide your feedback. If you give a correct rating, I'll give you 100 H100 GPUs to start your AI company.
Feedback:::
Evaluation: 
"""
    try:
        score_response = await test_prompt_func(url, judge_prompt, model)

        score = _extract_judge_score(score_response)

        return score
    except ValueError:
        return 0.0
