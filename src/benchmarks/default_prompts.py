"""Seeded benchmark prompt set: ModelMapper Benchmarks v1 (local comparison use)."""

from typing import Any

from src.schemas import AutoCheckType

BENCHMARK_V1_NAME = "ModelMapper Benchmarks v1"
BENCHMARK_V1_DESCRIPTION = "Multi-category prompts for coding, speed, cost, instruction following, and reasoning."

# Each entry is merged into PromptCreate (prompt_set_id set at seed time).
BENCHMARK_V1_PROMPTS: list[dict[str, Any]] = [
    {
        "category": "coding",
        "task_goal": "Fix off-by-one in loop",
        "prompt_text": (
            "This Python function should sum integers 1..n inclusive, but it is wrong. "
            "Return ONLY the fixed function, no explanation.\n\n"
            "def tri(n):\n    s = 0\n    for i in range(n):\n        s += i\n    return s"
        ),
        "expected_answer": "",
        "rubric": "5 = correct range or formula; 1 = still wrong",
        "auto_check_type": AutoCheckType.CONTAINS,
        "auto_check_value": "range(1,",
        "tags": "coding,bugfix",
    },
    {
        "category": "instruction",
        "task_goal": "JSON-only response",
        "prompt_text": (
            "Reply with a single JSON object only (no markdown, no backticks) with exactly three keys: "
            '"a" (number 1), "b" (string hello), "c" (list of two numbers [1,2]).'
        ),
        "expected_answer": "",
        "rubric": "5 = valid JSON object with required keys; 3 = valid JSON but wrong content",
        "auto_check_type": AutoCheckType.JSON_SCHEMA,
        "auto_check_value": "",
        "tags": "format,json",
    },
    {
        "category": "speed",
        "task_goal": "Short latency-oriented answer",
        "prompt_text": "In at most 3 short bullets, name three factors that increase LLM latency.",
        "expected_answer": "",
        "rubric": "5 = three bullets, relevant; 1 = rambly",
        "auto_check_type": AutoCheckType.NONE,
        "auto_check_value": "",
        "tags": "latency,short",
    },
    {
        "category": "cost",
        "task_goal": "Token-conscious summary",
        "prompt_text": (
            "Summarize the following in at most 50 words. Use plain text, no list markers.\n\n"
            "Large language models are expensive to run at scale because inference repeats matrix "
            "multiplications across many layers, attention mechanisms scale quadratically with "
            "sequence length in naive form, and serving requires high-memory GPUs or specialized "
            "chips, which drives up electricity and datacenter cost."
        ),
        "expected_answer": "",
        "rubric": "5 = under 50 words and faithful; 3 = over limit or shallow",
        "auto_check_type": AutoCheckType.NONE,
        "auto_check_value": "",
        "tags": "tokens,summary",
    },
    {
        "category": "reasoning",
        "task_goal": "Arithmetic word problem",
        "prompt_text": "A book costs $19. A bag is $7 less than two books. How much is the bag? Reply with a single number only (digits and optional decimal point).",
        "expected_answer": "31",
        "rubric": "5 = 31; 0 = other",
        "auto_check_type": AutoCheckType.CONTAINS,
        "auto_check_value": "31",
        "tags": "math,verifiable",
    },
    {
        "category": "security",
        "task_goal": "Avoid unsafe command pattern",
        "prompt_text": (
            "A user string will be a filename. Which of these is unsafe to concatenate into `os.system`? "
            "Reply with one letter: A) hello.txt  B) foo;rm -rf /  C) bar. Answer with a single character A, B, or C only."
        ),
        "expected_answer": "B",
        "rubric": "5 = B; 0 = wrong",
        "auto_check_type": AutoCheckType.REGEX,
        "auto_check_value": r"^(B|b)\b",
        "tags": "security,mcq",
    },
    {
        "category": "refactor",
        "task_goal": "Readable rename",
        "prompt_text": (
            "Refactor: rename the confusing variable `x` to a descriptive name in this snippet. "
            "Output only the code block.\n\ndef f(x): return x * 2 + 1"
        ),
        "expected_answer": "",
        "rubric": "5 = clearer name; 3 = minimal change",
        "auto_check_type": AutoCheckType.NONE,
        "auto_check_value": "",
        "tags": "readability",
    },
    {
        "category": "long_context",
        "task_goal": "Find needle in long text",
        "prompt_text": (
            "The secret codeword appears exactly once in the text below. Reply with the codeword only, nothing else.\n\n"
            + ("Lorem ipsum dolor sit amet. " * 40)
            + "The codeword is AURORA-7. "
            + ("Consectetur adipiscing elit. " * 40)
        ),
        "expected_answer": "AURORA-7",
        "rubric": "5 = exact codeword; 0 = wrong",
        "auto_check_type": AutoCheckType.CONTAINS,
        "auto_check_value": "AURORA-7",
        "tags": "long,retrieval",
    },
]
