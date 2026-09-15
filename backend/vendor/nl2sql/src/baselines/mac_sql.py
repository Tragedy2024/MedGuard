"""
B1: MAC-SQL Vanilla — original 3-Agent pipeline.

Supports two LLM modes per dataset:
  - 'fewshot': BIRD decomposition template ("Sub question N: ...")
  - 'zeroshot': Spider direct-SQL template (single SQL output)

Usage:
    baseline = MACSQLVanilla(data_path=..., tables_json_path=..., dataset_name='bird')
    # Few-shot (decomposition):
    result = baseline.decompose(item, mode='fewshot')
    # Zero-shot (direct SQL):
    result = baseline.decompose(item, mode='zeroshot')
"""
import os, sys
from typing import List, Dict, Optional

_VENDOR = os.path.join(os.path.dirname(__file__), '..', '..', 'vendor', 'MAC-SQL')
if _VENDOR not in sys.path:
    sys.path.insert(0, _VENDOR)

from core.chat_manager import ChatManager
from core.const import SYSTEM_NAME, SELECTOR_NAME, DECOMPOSER_NAME
from decomposer_parser import parse_qa_pairs


class MACSQLVanilla:
    """B1: MAC-SQL Vanilla — Selector → Decomposer → Refiner."""

    def __init__(self, data_path: str, tables_json_path: str,
                 model_name: str = "gpt-4o", dataset_name: str = "bird",
                 lazy: bool = True, without_selector: bool = False):
        self.data_path = data_path
        self.tables_json_path = tables_json_path
        self.model_name = model_name
        self.dataset_name = dataset_name
        self.chat_manager = ChatManager(
            data_path=data_path, tables_json_path=tables_json_path,
            log_path=f"./results/mac_sql_log_{os.getpid()}.txt",
            model_name=model_name, dataset_name=dataset_name,
            lazy=lazy, without_selector=without_selector,
        )

    def decompose(self, item: dict, skip_refiner: bool = False,
                  mode: str = 'fewshot') -> dict:
        """
        Run MAC-SQL on one query.

        Args:
            item: BIRD/Spider query dict
            skip_refiner: Skip SQL execution (saves ~50% time)
            mode: 'fewshot' (decomposition) or 'zeroshot' (direct SQL)

        Returns:
            {sub_queries, pred_sql, desc_str, fk_str, qa_pairs_raw, error}
        """
        if skip_refiner:
            return self._run_select_decompose(item, mode=mode)

        msg = self._build_message(item)
        self._run_chat(msg, mode=mode)
        return self._parse_output(msg)

    # ================================================================
    # Internal methods
    # ================================================================

    def _build_message(self, item: dict) -> dict:
        return {
            "idx": item.get('question_id', 0),
            "db_id": item['db_id'],
            "query": item['question'],
            "evidence": item.get('evidence', ''),
            "extracted_schema": {},
            "ground_truth": item.get('SQL', ''),
            "difficulty": item.get('difficulty', 'simple'),
            "send_to": SYSTEM_NAME,
        }

    def _run_chat(self, msg: dict, mode: str = 'fewshot'):
        """Run ChatManager with optional mode override."""
        decomposer = self.chat_manager.chat_group[1]
        orig_ds = decomposer.dataset_name
        decomposer.dataset_name = 'bird' if mode == 'fewshot' else 'spider'
        try:
            self.chat_manager.start(msg)
        finally:
            decomposer.dataset_name = orig_ds

    def _run_select_decompose(self, item: dict, mode: str = 'fewshot') -> dict:
        """Fast path: Selector + Decomposer only, no Refiner."""
        msg = {
            "idx": item.get('question_id', 0),
            "db_id": item['db_id'],
            "query": item['question'],
            "evidence": item.get('evidence', ''),
            "extracted_schema": {},
            "ground_truth": item.get('SQL', ''),
            "difficulty": item.get('difficulty', 'simple'),
            "send_to": SELECTOR_NAME,
        }

        # Mode override on Decomposer
        decomposer = self.chat_manager.chat_group[1]
        orig_ds = decomposer.dataset_name
        decomposer.dataset_name = 'bird' if mode == 'fewshot' else 'spider'

        try:
            self.chat_manager.chat_group[0].talk(msg)  # Selector
            msg['send_to'] = DECOMPOSER_NAME
            self.chat_manager.chat_group[1].talk(msg)  # Decomposer
        except Exception as e:
            return {'sub_queries': [], 'pred_sql': '', 'desc_str': '',
                    'fk_str': '', 'qa_pairs_raw': '', 'error': str(e)}
        finally:
            decomposer.dataset_name = orig_ds

        return self._parse_output(msg)

    def _parse_output(self, msg: dict) -> dict:
        """Extract sub_queries and pred_sql from MAC-SQL output."""
        qa_pairs = msg.get('qa_pairs', '')
        pred_sql = msg.get('final_sql', msg.get('pred', ''))
        desc_str = msg.get('desc_str', '')
        fk_str = msg.get('fk_str', '')

        if not qa_pairs and not pred_sql:
            return {'sub_queries': [], 'pred_sql': '', 'desc_str': desc_str,
                    'fk_str': fk_str, 'qa_pairs_raw': '', 'error': 'No output'}

        tasks = parse_qa_pairs(qa_pairs) if qa_pairs else []
        sub_queries = [{'id': t.id, 'description': t.description, 'sql': t.sql}
                       for t in tasks]

        # Fallback: zeroshot/Spider produces direct SQL → single sub-query
        if not sub_queries and pred_sql and 'error' not in pred_sql.lower():
            sub_queries = [{'id': 0, 'description': 'Direct SQL', 'sql': pred_sql}]

        return {'sub_queries': sub_queries, 'pred_sql': pred_sql,
                'desc_str': desc_str, 'fk_str': fk_str,
                'qa_pairs_raw': qa_pairs, 'error': None}
