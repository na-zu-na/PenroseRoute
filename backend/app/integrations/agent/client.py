import json
import os
from typing import Protocol
from .contracts import ExplanationFact, ExplanationOutline
from .prompts import SYSTEM_PROMPT


class ExplanationClient(Protocol):
    def arrange(self, facts: tuple[ExplanationFact, ...]) -> ExplanationOutline: ...


class BedrockExplanationClient:
    """Use the configured AWS credential chain; no client-supplied prompt or model ID."""
    def __init__(self, model_id: str | None = None, client=None, region_name: str | None = None,
                 endpoint_url: str | None = None, connect_timeout: float = 3, read_timeout: float = 10):
        self.model_id = model_id or os.environ["BEDROCK_MODEL_ID"]
        self.client = client
        self.region_name = region_name or os.environ.get("AWS_REGION", "ap-southeast-1")
        self.endpoint_url = endpoint_url
        self.connect_timeout, self.read_timeout = connect_timeout, read_timeout

    def _client(self):
        # Lazy initialization places credential/endpoint failures inside the graph's
        # explanation fallback boundary, after the validated solve has completed.
        if self.client is None:
            import boto3
            from botocore.config import Config
            self.client = boto3.client("bedrock-runtime", region_name=self.region_name,
                endpoint_url=self.endpoint_url, config=Config(connect_timeout=self.connect_timeout,
                    read_timeout=self.read_timeout, retries={"total_max_attempts": 1}))
        return self.client

    def arrange(self, facts):
        response = self._client().converse(
            modelId=self.model_id, system=[{"text": SYSTEM_PROMPT}],
            messages=[{"role": "user", "content": [{"text": json.dumps([f.model_dump() for f in facts], ensure_ascii=False)}]}],
            inferenceConfig={"maxTokens": 512, "temperature": 0},
            toolConfig={"tools": [{"toolSpec": {"name": "arrange_explanation",
                         "description": "Order the provided verified facts without changing them",
                         "inputSchema": {"json": ExplanationOutline.model_json_schema()}}}],
                        "toolChoice": {"tool": {"name": "arrange_explanation"}}})
        calls = [b["toolUse"] for b in response["output"]["message"]["content"] if "toolUse" in b]
        if len(calls) != 1 or calls[0]["name"] != "arrange_explanation":
            raise ValueError("Unexpected model tool call")
        return ExplanationOutline.model_validate(calls[0]["input"])
