import json
import unittest
from unittest.mock import patch
from quiz_randomization import randomized_question


class QuizRandomizationTests(unittest.TestCase):
    def test_shuffle_preserves_correct_answer(self):
        source = {"type": "multiple_choice", "question": "q", "explanation": "e", "choices": ["A", "B", "C", "D"], "answer": "A"}
        with patch("quiz_randomization.secrets.SystemRandom.shuffle", side_effect=lambda values: values.reverse()):
            result = json.loads(randomized_question(json.dumps(source)))
        self.assertEqual(result["choices"], ["D", "C", "B", "A"])
        self.assertEqual(result["answer"], "A")

    def test_duplicate_choices_rejected(self):
        with self.assertRaises(ValueError):
            randomized_question(json.dumps({"type": "multiple_choice", "question": "q", "explanation": "e", "choices": ["A"] * 4, "answer": "A"}))

    def test_boolean_answer_keeps_truth(self):
        for value in (True, False):
            result = json.loads(randomized_question(json.dumps({"type": "true_false", "question": "q", "explanation": "e", "answer": value})))
            self.assertIs(result["answer"], value)
        with self.assertRaises(ValueError):
            randomized_question(json.dumps({"type": "true_false", "question": "q", "explanation": "e", "answer": "true"}))
