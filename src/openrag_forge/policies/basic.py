from __future__ import annotations

import re


def detect_request_risks(question: str) -> list[str]:
    normalized = question.lower()
    patterns = {
        "refund_promise": r"promise|guarantee|will i get.*refund|一定.*退款|保证.*赔偿",
        "legal_conclusion": r"违法|illegal|law.?breaking|谁负责|认定.*责任",
        "account_decision": r"查.*账户|决定.*封禁|close.*account|approve.*refund",
    }
    return [code for code, pattern in patterns.items() if re.search(pattern, normalized)]

