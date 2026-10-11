"""Fast unit checks: prevent lost text/truncated GLM output. Expected <1 s; max 5 s."""
import importlib.util
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch
class Reply:
    def __init__(self,payload): self.payload=payload
    def __enter__(self): return self
    def __exit__(self,*args): return False
    def read(self): return json.dumps(self.payload).encode()
spec=importlib.util.spec_from_file_location("converter",Path(__file__).resolve().parents[1]/"app/convert.py")
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
class GlmTests(unittest.TestCase):
    def test_anthropic_cleanup_ignores_thinking(self):
        response=Reply({"content":[{"type":"thinking","thinking":"hidden"},{"type":"text","text":"clean"}],"stop_reason":"end_turn"})
        with patch.dict(os.environ,{"LLM_API_STYLE":"anthropic"}),patch.object(m.urllib.request,"urlopen",return_value=response):
            self.assertEqual(m.llm_cleanup("text",base_url="https://example.invalid/anthropic",api_key="test",model="glm-5.3-flash"),"clean\n")
    def test_truncated_cleanup_fails(self):
        response=Reply({"content":[{"type":"text","text":"partial"}],"stop_reason":"max_tokens"})
        with patch.dict(os.environ,{"LLM_API_STYLE":"anthropic"}),patch.object(m.urllib.request,"urlopen",return_value=response):
            with self.assertRaisesRegex(RuntimeError,"truncated"):
                m.llm_cleanup("text",base_url="https://example.invalid/anthropic",api_key="test",model="glm-5.3-flash")
if __name__=="__main__": unittest.main()
