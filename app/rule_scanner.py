"""Client for the Sublime Security free Analyzer API."""

import base64
import logging
import os
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)
DEFAULT_ANALYZER_URL = "https://analyzer.sublime.security"


class SublimeAnalyzer:
    """Unauthenticated client for the Sublime Security free Analyzer API."""

    def __init__(self, base_url: Optional[str] = None):
        self.base_url = base_url or os.environ.get(
            "SUBLIME_API_BASE", DEFAULT_ANALYZER_URL
        )

    async def analyze_message(
        self,
        eml_bytes: bytes,
        run_all_detection_rules: bool = True,
        run_active_detection_rules: bool = False,
    ) -> Dict[str, Any]:
        """Submit a raw .eml to the Sublime Analyzer and return the JSON response.

        The free analyzer API at https://analyzer.sublime.security does not require
        an API key. A descriptive User-Agent header is required.

        Args:
            eml_bytes: Raw RFC 5322 email message bytes.
            run_all_detection_rules: Run every detection rule from all public Feeds.
            run_active_detection_rules: Run active rules from your Sublime org
                (requires authentication; ignored on the free endpoint).

        Returns:
            Parsed JSON response containing ``rule_results`` and ``query_results``.
        """
        b64_msg = base64.b64encode(eml_bytes).decode("ascii")
        logger.info(
            "Sending %d raw bytes (%d base64 chars) to %s/v0/messages/analyze",
            len(eml_bytes),
            len(b64_msg),
            self.base_url,
        )
        payload = {
            "raw_message": b64_msg,
            "run_all_detection_rules": run_all_detection_rules,
            "run_active_detection_rules": run_active_detection_rules,
            "run_all_insights": True,
        }
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                f"{self.base_url}/v0/messages/analyze",
                json=payload,
                headers={
                    "Content-Type": "application/json",
                    "User-Agent": "sublime-email-analyzer/1.0",
                },
            )

            logger.info(
                "Analyzer response: HTTP %s, content-type=%s, len=%d",
                response.status_code,
                response.headers.get("content-type", "unknown"),
                len(response.text),
            )

            # Handle non-JSON error responses gracefully
            if response.status_code >= 400:
                body = response.text[:500]
                raise RuntimeError(
                    f"Sublime Analyzer returned HTTP {response.status_code}: {body}"
                )

            content_type = response.headers.get("content-type", "")
            if "application/json" not in content_type:
                body = response.text[:500]
                raise RuntimeError(
                    f"Sublime Analyzer returned unexpected content-type ({content_type}): {body}"
                )

            return response.json()
