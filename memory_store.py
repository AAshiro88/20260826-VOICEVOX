# -*- coding: utf-8 -*-
"""VOICEVOX 對話的長期記憶庫（chroma_db 向量儲存）。

設計原則：
- 一切讀寫包在 try/except 內，任何失敗都回退為「無記憶」，
  絕不阻擋語音對話主流程。
- 以「對話檔名 stem」（chat_日期_時間_毫秒）作為隔離鍵 chat_id，
  每個對話只檢索自己的記憶，跨對話不互相干擾。
- 不使用外部 embedding API：採用 chroma 內建的 ONNX embedding
  （all-MiniLM-L6-v2），首次寫入前會在系統快取目錄自動下載約 80MB 模型。
"""

import difflib
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import chromadb
from chromadb.config import Settings

COLLECTION_NAME = "voicechat_memory"
# 事實語句的最短長度；太短的句子視為無意義不寫入
MIN_FACT_LEN = 4
# 與既有記憶重複的相似度門檻（超過即視為重複略過）
DUP_RATIO = 0.85
# 每次檢索回傳的事實筆數上限
RETRIEVE_K = 5


def now_iso():
    """回傳目前 UTC 時間（ISO 格式，供 metadata 排序與除錯用）。"""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def clean_fact(text):
    """清理單一事實：去首尾空白、壓縮連續空白、去掉常見條列符號。"""
    t = " ".join((text or "").split())
    return t.lstrip("-•·　*# ").strip()


class MemoryStore:
    """chroma_db 長期記憶庫；初始化失敗時自動停用。"""

    def __init__(self, base_dir):
        self.base_dir = Path(base_dir)
        self._col = None
        try:
            settings = Settings(anonymized_telemetry=False)
            client = chromadb.PersistentClient(
                path=str(self.base_dir / "chroma_db"), settings=settings
            )
            self._col = client.get_or_create_collection(COLLECTION_NAME)
        except Exception:
            self._col = None

    @property
    def enabled(self):
        return self._col is not None

    @staticmethod
    def chat_id(path):
        """由對話檔案路徑取得隔離鍵（不含副檔名的檔名）。"""
        return Path(path).stem

    def facts_for(self, chat_id):
        """列出某對話目前已儲存的事實（純文字清單）。"""
        if not self.enabled:
            return []
        try:
            res = self._col.get(
                where={"chat_id": chat_id},
                include=["documents", "metadatas"],
            )
            return [d for d in (res.get("documents") or []) if d]
        except Exception:
            return []

    def add_facts(self, chat_id, facts, lang, source):
        """寫入多則事實；與既有記憶重複時略過。

        facts：文字清單。lang 為對話語言；source 為 "auto" 或 "manual"。
        回傳實際新增筆數（失败時回傳 0）。
        """
        if not self.enabled or not facts:
            return 0
        existing = self.facts_for(chat_id)
        to_add = []
        ids = []
        for raw in facts:
            fact = clean_fact(raw)
            if not fact or len(fact) < MIN_FACT_LEN:
                continue
            if any(self._similar(fact, old) for old in existing):
                continue
            existing.append(fact)
            to_add.append(fact)
            ids.append(f"{chat_id}_{uuid4().hex}")
        if not to_add:
            return 0
        created = now_iso()
        try:
            self._col.add(
                ids=ids,
                documents=to_add,
                metadatas=[
                    {
                        "chat_id": chat_id,
                        "lang": lang,
                        "source": source,
                        "created_at": created,
                    }
                ]
                * len(to_add),
            )
            return len(to_add)
        except Exception:
            return 0

    def retrieve(self, chat_id, query, k=RETRIEVE_K):
        """以 query 文字檢索該對話的記憶，回傳 [(距離, 事實文字), ...] 升冪排序。

        距離越小表示與查詢越相關；失敗時回傳空清單。
        """
        if not self.enabled or not (query or "").strip():
            return []
        try:
            res = self._col.query(
                query_texts=[query.strip()],
                n_results=k,
                where={"chat_id": chat_id},
                include=["documents", "distances"],
            )
            docs = (res.get("documents") or [[]])[0] or []
            dists = (res.get("distances") or [[]])[0] or []
            merged = [
                (float(d), doc)
                for doc, d in zip(docs, dists)
                if doc and d is not None
            ]
            merged.sort(key=lambda item: item[0])
            return merged
        except Exception:
            return []

    def count(self, chat_id):
        """回傳某對話已儲存的事實筆數。"""
        return len(self.facts_for(chat_id))

    def delete_chat(self, chat_id):
        """刪除某對話的全部記憶（刪除對話檔案時一併呼叫）。"""
        if not self.enabled or not chat_id:
            return
        try:
            self._col.delete(where={"chat_id": chat_id})
        except Exception:
            pass

    def list_facts(self, chat_id):
        """列出某對話已儲存的記憶，回傳 [(id, 事實文字, metadata), ...]。

        保留 id 供「檢視／編輯／刪除」對話框定位單筆記憶。
        """
        if not self.enabled:
            return []
        try:
            res = self._col.get(
                where={"chat_id": chat_id},
                include=["documents", "metadatas"],
            )
            ids = res.get("ids") or []
            docs = res.get("documents") or []
            metas = res.get("metadatas") or []
            return [
                (i, doc, (m or {}))
                for i, doc, m in zip(ids, docs, metas)
                if doc
            ]
        except Exception:
            return []

    def delete_fact(self, chat_id, fact_id):
        """刪除某對話的一則記憶；不存在或失敗時靜默略過。"""
        if not self.enabled or not fact_id:
            return
        try:
            self._col.delete(ids=[fact_id])
        except Exception:
            pass

    def update_fact(self, chat_id, fact_id, new_text):
        """更新一則記憶的文字並重新整理 embedding。

        文字太短或與其他筆記憶重複時不更新（排除自己）。回傳更新後的文字，
        未更新時回傳 None。
        """
        if not self.enabled or not fact_id:
            return None
        fact = clean_fact(new_text)
        if not fact or len(fact) < MIN_FACT_LEN:
            return None
        target = None
        others = []
        for i, doc, meta in self.list_facts(chat_id):
            if i == fact_id:
                target = (i, doc, meta)
            else:
                others.append((i, doc, meta))
        if target is None:
            return None
        if any(self._similar(fact, old) for _i, old, _m in others):
            return None
        try:
            meta = dict(target[2])
            meta["created_at"] = now_iso()
            self._col.update(
                ids=[fact_id],
                documents=[fact],
                metadatas=[meta],
            )
            return fact
        except Exception:
            return None

    @staticmethod
    def _similar(a, b):
        """以字元比例相似度判斷兩則事實是否重複。"""
        return difflib.SequenceMatcher(None, a, b).ratio() >= DUP_RATIO