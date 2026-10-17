"""Randomize answer positions without changing the correct answer."""
import json
import secrets


def randomized_question(line):
    question = json.loads(line)
    if not isinstance(question, dict):
        raise ValueError("Expected a question object")
    if (not isinstance(question.get("question"), str) or not question["question"].strip()
            or not isinstance(question.get("explanation"), str)):
        raise ValueError("Invalid question text or explanation")
    if question.get("type") == "multiple_choice":
        choices = question.get("choices")
        if (not isinstance(choices, list) or len(choices) != 4
                or not all(isinstance(choice, str) for choice in choices)
                or len(set(choices)) != 4 or question.get("answer") not in choices):
            raise ValueError("Invalid multiple-choice question")
        secrets.SystemRandom().shuffle(choices)
    elif question.get("type") == "true_false":
        if type(question.get("answer")) is not bool:
            raise ValueError("Invalid true/false answer")
    else:
        raise ValueError("Unsupported question type")
    return json.dumps(question, ensure_ascii=False)
