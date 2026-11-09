"""知识库模块：读取 knowledge/ 下的测试方法文档并分块，提供中文关键词检索（默认）与 ChromaDB 向量检索（可选）。"""
import math
import os
import re
from collections import Counter

import jieba

import config

# Markdown 标题行
_HEADING_RE = re.compile(r"^#{1,6}\s+(.*)$")
# 分块长度上限（字符），超过则按段落再切分
_MAX_CHUNK_LEN = 600
# 低于该长度的碎块并入前一块
_MIN_CHUNK_LEN = 80


def _tokenize(text):
    """jieba 分词并过滤标点与空白，词统一转小写。"""
    return [t for t in jieba.lcut(text.lower()) if t.strip() and not re.fullmatch(r"[\W_]+", t)]


def _split_markdown(text, source):
    """按 Markdown 标题分块；过长的块按空行段落再切分，过短的碎块并入前一块。"""
    chunks = []
    current_title = ""
    current_lines = []

    def flush():
        body = "\n".join(current_lines).strip()
        if not body and not current_title:
            return
        content = (f"【{current_title}】\n{body}" if current_title else body).strip()
        if content:
            chunks.append({"source": source, "title": current_title, "content": content})

    for line in text.splitlines():
        m = _HEADING_RE.match(line.strip())
        if m:
            flush()
            current_title = m.group(1).strip()
            current_lines = []
        else:
            current_lines.append(line)
    flush()

    # 过长块按段落二次切分
    fine = []
    for c in chunks:
        if len(c["content"]) <= _MAX_CHUNK_LEN:
            fine.append(c)
            continue
        paras = [p.strip() for p in re.split(r"\n\s*\n", c["content"]) if p.strip()]
        buf = ""
        for p in paras:
            if buf and len(buf) + len(p) > _MAX_CHUNK_LEN:
                fine.append({"source": c["source"], "title": c["title"], "content": buf})
                buf = p
            else:
                buf = f"{buf}\n{p}" if buf else p
        if buf:
            fine.append({"source": c["source"], "title": c["title"], "content": buf})

    # 过短碎块并入前一块，避免检索粒度太碎
    merged = []
    for c in fine:
        if merged and len(c["content"]) < _MIN_CHUNK_LEN:
            merged[-1]["content"] += "\n" + c["content"]
        else:
            merged.append(dict(c))
    return merged


class KnowledgeBase:
    """测试方法知识库：build() 分块并建立索引，retrieve() 返回相关段落。

    mode="keyword"：jieba 分词 + TF-IDF 式词频打分（纯 Python，中文可靠，默认）；
    mode="vector" ：ChromaDB 向量检索（数据持久化到项目下 chroma_db/ 目录）。
    """

    def __init__(self, mode="keyword", persist_dir=None):
        if mode not in ("keyword", "vector"):
            raise ValueError("mode 只能是 'keyword' 或 'vector'")
        self.mode = mode
        self.persist_dir = persist_dir or config.CHROMA_DIR
        self.chunks = []
        # 关键词模式索引
        self._tf = []        # 每块的词频 Counter
        self._df = Counter()  # 每个词出现在多少块中
        # 向量模式对象（用到时才惰性初始化）
        self._chroma_client = None
        self._collection = None

    # ---------------- 构建 ----------------
    def build(self, knowledge_dir=None):
        """读取 knowledge/*.md 并分块建索引，返回 self。"""
        knowledge_dir = knowledge_dir or config.KNOWLEDGE_DIR
        self.chunks = []
        for fname in sorted(os.listdir(knowledge_dir)):
            if not fname.endswith(".md"):
                continue
            path = os.path.join(knowledge_dir, fname)
            with open(path, "r", encoding="utf-8") as f:
                self.chunks.extend(_split_markdown(f.read(), fname))
        if self.mode == "keyword":
            self._build_keyword_index()
        else:
            self._build_vector_index()
        return self

    def _build_keyword_index(self):
        """统计每个块的词频与每个词的文档频率。"""
        self._tf = [Counter(_tokenize(c["content"])) for c in self.chunks]
        self._df = Counter()
        for tf in self._tf:
            for term in tf:
                self._df[term] += 1

    def _build_vector_index(self):
        """把分块写入 ChromaDB（惰性导入，关键词模式不加载该依赖）。"""
        import chromadb
        os.makedirs(self.persist_dir, exist_ok=True)
        self._chroma_client = chromadb.PersistentClient(path=self.persist_dir)
        self._collection = self._chroma_client.get_or_create_collection(
            name="testcase_kb", metadata={"hnsw:space": "cosine"})
        if self._collection.count() == 0 and self.chunks:
            self._collection.add(
                ids=[f"chunk_{i}" for i in range(len(self.chunks))],
                documents=[c["content"] for c in self.chunks],
                metadatas=[{"source": c["source"], "title": c["title"]} for c in self.chunks],
            )

    # ---------------- 检索 ----------------
    def retrieve(self, query, top_k=3):
        """检索与 query 最相关的 top_k 个知识块，返回文本列表。"""
        if not self.chunks:
            return []
        if self.mode == "vector":
            return self._retrieve_vector(query, top_k)
        return self._retrieve_keyword(query, top_k)

    def _retrieve_keyword(self, query, top_k):
        """jieba 分词 + TF-IDF（BM25 风格 idf）词频打分的中文关键词检索。"""
        q_terms = _tokenize(query)
        if not q_terms:
            return []
        n = len(self.chunks)
        q_tf = Counter(q_terms)
        scored = []
        for i, tf in enumerate(self._tf):
            doc_len = sum(tf.values()) or 1
            score = 0.0
            for term, qf in q_tf.items():
                df = self._df.get(term, 0)
                if df == 0:
                    continue
                idf = math.log((n - df + 0.5) / (df + 0.5) + 1.0)
                tf_norm = tf.get(term, 0) / doc_len
                score += qf * idf * tf_norm
            scored.append((score, i))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [self.chunks[i]["content"] for score, i in scored[:top_k] if score > 0]

    def _retrieve_vector(self, query, top_k):
        if self._collection is None or self._collection.count() == 0:
            return []
        res = self._collection.query(
            query_texts=[query], n_results=min(top_k, self._collection.count()))
        return (res.get("documents") or [[]])[0]


_default_kb = None


def build(mode="keyword"):
    """构建并缓存默认知识库实例，返回该实例。"""
    global _default_kb
    _default_kb = KnowledgeBase(mode=mode).build()
    return _default_kb


def retrieve(query, top_k=3, mode="keyword"):
    """使用默认知识库检索；首次调用或模式变化时自动重建。"""
    global _default_kb
    if _default_kb is None or _default_kb.mode != mode:
        build(mode)
    return _default_kb.retrieve(query, top_k=top_k)
