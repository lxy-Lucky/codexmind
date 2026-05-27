from __future__ import annotations

"""
Parser Service v2
-----------------
输出两类数据：
  1. chunks     - 结构化文档（用于 embed + BM25）
  2. call_refs  - 调用关系（用于构建 Call Graph）

Chunk 结构化文档格式：
  [FILE] com/example/payment/OrderService.java
  [CLASS] OrderService
  [METHOD] processPayment
  [SIGNATURE] public void processPayment(Order order, PaymentMethod method)
  [ANNOTATIONS] @Transactional
  [CALLS] PaymentGateway.charge, RetryHandler.retry
  [JAVADOC] 处理订单支付，校验金额后调用网关
  <原始代码体>

信噪比从裸代码的 ~30% 提升到 ~80%，无需 LLM。
"""

import logging
import re
from functools import lru_cache
from typing import Any, Optional

from app.core.config import settings

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _get_tokenizer():
    # 与 EMBEDDING_MODEL 共用同一个 tokenizer，确保切窗判定与向量化截断口径一致。
    # bge-m3 基于 XLM-RoBERTa，CJK 通常 1 字 ≈ 1 token，cl100k_base 会高估 2-3 倍。
    from transformers import AutoTokenizer
    tok = AutoTokenizer.from_pretrained(settings.EMBEDDING_MODEL)
    # 仅用于 token 计数（用于"要不要切窗"判定）。超过 model max_length 不喂模型，
    # 不会有 indexing error；设大上限消除 transformers 的 "sequence too long" warning。
    tok.model_max_length = int(1e9)
    return tok


def _count_tokens(text: str) -> int:
    return len(_get_tokenizer().encode(text, add_special_tokens=False))


# ── tree-sitter ────────────────────────────────────────────────────────────────

@lru_cache(maxsize=None)
def _get_parser(language: str) -> Optional[Any]:
    """仅支持 Java 和 JS/TS（XML 走 sliding-window fallback，无需 parser）"""
    try:
        import tree_sitter_java
        import tree_sitter_javascript, tree_sitter_typescript
        from tree_sitter import Language, Parser
        lang_map = {
            "java":       tree_sitter_java.language(),
            "javascript": tree_sitter_javascript.language(),
            "typescript": tree_sitter_typescript.language_typescript(),
        }
        if language not in lang_map:
            return None
        return Parser(Language(lang_map[language]))
    except Exception as e:
        logger.debug("tree-sitter not available for %s: %s", language, e)
        return None


NODE_TYPES: dict[str, list[str]] = {
    "java":       ["method_declaration", "constructor_declaration", "class_declaration"],
    "javascript": ["function_declaration", "arrow_function", "method_definition", "class_declaration"],
    "typescript": ["function_declaration", "arrow_function", "method_definition", "class_declaration"],
}

# ── 正则 fallback ──────────────────────────────────────────────────────────────

_JAVA_METHOD_RE = re.compile(
    r'^([ \t]*)(?:(?:public|private|protected|static|final|synchronized|abstract'
    r'|default|override|async|native|strictfp)[ \t]+)+'
    r'(?:(?:<[^>]+>[ \t]+)?[\w\[\]<>,.?]+[ \t]+)'
    r'(?!if\b|for\b|while\b|switch\b|catch\b|else\b|try\b|new\b)'
    r'([a-zA-Z_]\w*)[ \t]*\([^)]{0,200}\)'
    r'(?:[ \t]+throws[ \t]+[\w,\s]+)?[ \t]*\{',
    re.MULTILINE,
)

_PYTHON_DEF_RE = re.compile(
    r'^([ \t]*)(async[ \t]+)?def[ \t]+([a-zA-Z_]\w*)[ \t]*\(',
    re.MULTILINE,
)

_JS_FUNC_RE = re.compile(
    r'^([ \t]*)(?:export[ \t]+(?:default[ \t]+)?)?(?:async[ \t]+)?'
    r'(?:'
    r'function[ \t]+([a-zA-Z_]\w*)[ \t]*\([^)]{0,200}\)[ \t]*\{'
    r'|(?:const|let|var)[ \t]+([a-zA-Z_]\w*)[ \t]*=[ \t]*(?:async[ \t]+)?'
    r'(?:\([^)]{0,200}\)|[a-zA-Z_]\w*)[ \t]*=>[ \t]*\{'
    r')',
    re.MULTILINE,
)

# ── Type Tracker（Java 字段/局部变量类型提取）─────────────────────────────────

def _extract_field_types(class_node, src: bytes) -> dict[str, str]:
    """
    从 class_declaration 中提取字段声明，返回 {field_name: simple_type_name}。
    用于 _walk_java_calls 将 receiver 变量名解析到真实类名（如 userMapper → UserMapper）。
    只取简单类名（去包名 + 去泛型），足以匹配 symbol_map。
    """
    field_types: dict[str, str] = {}
    for child in class_node.children:
        if child.type != "class_body":
            continue
        for member in child.children:
            if member.type != "field_declaration":
                continue
            type_node = member.child_by_field_name("type")
            if not type_node:
                continue
            type_text = src[type_node.start_byte:type_node.end_byte].decode("utf-8", "replace")
            # 去包名（取最后一段）+ 去泛型（取 < 之前）
            simple = type_text.rsplit(".", 1)[-1].split("<")[0].strip()
            if not simple:
                continue
            for decl in member.children:
                if decl.type != "variable_declarator":
                    continue
                name_node = decl.child_by_field_name("name")
                if name_node:
                    fname = src[name_node.start_byte:name_node.end_byte].decode("utf-8", "replace")
                    field_types[fname] = simple
    return field_types


def _extract_local_var_types(method_node, src: bytes) -> dict[str, str]:
    """
    从 method_declaration 中提取形参 + 局部变量声明，返回 {var_name: simple_type_name}。
    与 _extract_field_types 格式一致，合并后传给 _walk_java_calls。
    """
    var_types: dict[str, str] = {}

    def _walk_vars(node) -> None:
        if node.type == "local_variable_declaration":
            type_node = node.child_by_field_name("type")
            if type_node:
                type_text = src[type_node.start_byte:type_node.end_byte].decode("utf-8", "replace")
                simple = type_text.rsplit(".", 1)[-1].split("<")[0].strip()
                for child in node.children:
                    if child.type == "variable_declarator":
                        name_node = child.child_by_field_name("name")
                        if name_node and simple:
                            vname = src[name_node.start_byte:name_node.end_byte].decode("utf-8", "replace")
                            var_types[vname] = simple
        elif node.type == "formal_parameter":
            type_node = node.child_by_field_name("type")
            name_node = node.child_by_field_name("name")
            if type_node and name_node:
                type_text = src[type_node.start_byte:type_node.end_byte].decode("utf-8", "replace")
                simple = type_text.rsplit(".", 1)[-1].split("<")[0].strip()
                pname = src[name_node.start_byte:name_node.end_byte].decode("utf-8", "replace")
                if simple:
                    var_types[pname] = simple
        for child in node.children:
            _walk_vars(child)

    _walk_vars(method_node)
    return var_types


# ── JS/TS Type Tracker（import 解析 + 字段/变量类型推断）──────────────────────

def _extract_js_imports(root_node, src: bytes) -> dict[str, str]:
    """
    从 AST 根节点提取 ES6 import 和 CommonJS require，返回 {本地绑定名: 推断类型名}。
    仅处理顶层语句（import 必然在顶层）。
    """
    type_map: dict[str, str] = {}
    for child in root_node.children:
        # ── ES6: import { Foo } from '...'; import Bar from '...'
        if child.type == "import_statement":
            for clause in child.children:
                if clause.type == "import_clause":
                    for spec in clause.children:
                        if spec.type == "identifier":
                            # default import: import FooService from '...'
                            name = src[spec.start_byte:spec.end_byte].decode("utf-8", "replace")
                            type_map[name] = name
                        elif spec.type == "named_imports":
                            for imp in spec.children:
                                if imp.type == "import_specifier":
                                    alias_node = imp.child_by_field_name("alias")
                                    name_node = imp.child_by_field_name("name")
                                    if alias_node:
                                        local = src[alias_node.start_byte:alias_node.end_byte].decode("utf-8", "replace")
                                        orig = src[name_node.start_byte:name_node.end_byte].decode("utf-8", "replace") if name_node else local
                                    elif name_node:
                                        local = src[name_node.start_byte:name_node.end_byte].decode("utf-8", "replace")
                                        orig = local
                                    else:
                                        continue
                                    type_map[local] = orig
                        elif spec.type == "namespace_import":
                            for ns_child in spec.children:
                                if ns_child.type == "identifier":
                                    name = src[ns_child.start_byte:ns_child.end_byte].decode("utf-8", "replace")
                                    type_map[name] = name
                                    break

        # ── CommonJS: const Foo = require('...'); const { Foo } = require('...')
        elif child.type in ("lexical_declaration", "variable_declaration"):
            for decl in child.children:
                if decl.type != "variable_declarator":
                    continue
                value_node = decl.child_by_field_name("value")
                if not value_node or value_node.type != "call_expression":
                    continue
                func_node = value_node.child_by_field_name("function")
                if not func_node:
                    continue
                func_text = src[func_node.start_byte:func_node.end_byte].decode("utf-8", "replace")
                if func_text != "require":
                    continue
                name_node = decl.child_by_field_name("name")
                if not name_node:
                    continue
                if name_node.type == "identifier":
                    # const Foo = require('...')
                    name = src[name_node.start_byte:name_node.end_byte].decode("utf-8", "replace")
                    type_map[name] = name
                elif name_node.type == "object_pattern":
                    # const { Foo, Bar } = require('...')
                    for prop in name_node.children:
                        if prop.type == "shorthand_property_identifier_pattern":
                            pname = src[prop.start_byte:prop.end_byte].decode("utf-8", "replace")
                            type_map[pname] = pname
                        elif prop.type == "pair_pattern":
                            val = prop.child_by_field_name("value")
                            if val:
                                pname = src[val.start_byte:val.end_byte].decode("utf-8", "replace")
                                type_map[pname] = pname
    return type_map


def _extract_js_field_types(class_node, src: bytes) -> dict[str, str]:
    """
    从 JS/TS class 中提取字段类型，返回 {字段名: 类型名}。
    支持: TS 类型注解字段、TS 构造函数参数类型、new 表达式推断。
    """
    field_types: dict[str, str] = {}
    for child in class_node.children:
        if child.type != "class_body":
            continue
        for member in child.children:
            # TS 类字段: public userService: UserService
            if member.type in ("public_field_definition", "property_definition"):
                _try_extract_ts_typed_field(member, src, field_types)

            # 构造函数参数类型: constructor(private svc: UserService)
            elif member.type == "method_definition":
                name_node = member.child_by_field_name("name")
                if name_node:
                    mname = src[name_node.start_byte:name_node.end_byte].decode("utf-8", "replace")
                    if mname == "constructor":
                        params = member.child_by_field_name("parameters")
                        if params:
                            _extract_ts_constructor_param_types(params, src, field_types)
                        body = member.child_by_field_name("body")
                        if body:
                            _extract_new_expr_types(body, src, field_types)
    return field_types


def _try_extract_ts_typed_field(node, src: bytes, out: dict[str, str]) -> None:
    """从 TS 类字段声明提取 name: Type 映射。"""
    name_node = node.child_by_field_name("name")
    if not name_node:
        return
    fname = src[name_node.start_byte:name_node.end_byte].decode("utf-8", "replace")
    type_ann = node.child_by_field_name("type") or _find_child_type(node, "type_annotation")
    if type_ann:
        tname = _extract_type_name_from_annotation(type_ann, src)
        if tname:
            out[fname] = tname


def _extract_ts_constructor_param_types(params_node, src: bytes, out: dict[str, str]) -> None:
    """从 TS 构造函数参数提取类型: constructor(private userService: UserService)"""
    for param in params_node.children:
        # TS: accessibility_modifier + identifier + type_annotation
        if param.type not in ("required_parameter", "formal_parameter", param.type):
            continue
        pname = None
        ptype = None
        for c in param.children:
            if c.type == "identifier":
                pname = src[c.start_byte:c.end_byte].decode("utf-8", "replace")
            elif c.type == "type_annotation":
                ptype = _extract_type_name_from_annotation(c, src)
        if pname and ptype:
            out[pname] = ptype


def _extract_new_expr_types(body_node, src: bytes, out: dict[str, str]) -> None:
    """遍历方法体/构造函数体，从 this.x = new Foo() 或 const x = new Foo() 中推断类型。"""
    def walk(node):
        # this.field = new Foo()
        if node.type == "assignment_expression":
            left = node.child_by_field_name("left")
            right = node.child_by_field_name("right")
            if left and right and right.type == "new_expression":
                ctor_type = _extract_new_ctor_name(right, src)
                if ctor_type and left.type == "member_expression":
                    obj = left.child_by_field_name("object")
                    prop = left.child_by_field_name("property")
                    if obj and prop:
                        obj_text = src[obj.start_byte:obj.end_byte].decode("utf-8", "replace")
                        if obj_text == "this":
                            fname = src[prop.start_byte:prop.end_byte].decode("utf-8", "replace")
                            out[fname] = ctor_type
        # const x = new Foo()
        elif node.type == "variable_declarator":
            name_node = node.child_by_field_name("name")
            value_node = node.child_by_field_name("value")
            if name_node and value_node and value_node.type == "new_expression":
                ctor_type = _extract_new_ctor_name(value_node, src)
                if ctor_type and name_node.type == "identifier":
                    vname = src[name_node.start_byte:name_node.end_byte].decode("utf-8", "replace")
                    out[vname] = ctor_type
        for child in node.children:
            walk(child)
    walk(body_node)


def _extract_new_ctor_name(new_node, src: bytes) -> str | None:
    """从 new_expression 节点提取构造函数类名。"""
    ctor = new_node.child_by_field_name("constructor")
    if ctor is None:
        for c in new_node.children:
            if c.type == "identifier":
                ctor = c
                break
    if ctor:
        text = src[ctor.start_byte:ctor.end_byte].decode("utf-8", "replace")
        return text.split(".")[-1].split("<")[0].strip() or None
    return None


def _extract_type_name_from_annotation(ann_node, src: bytes) -> str | None:
    """从 type_annotation 节点提取简单类型名（去泛型）。"""
    for child in ann_node.children:
        if child.type in ("type_identifier", "identifier", "generic_type"):
            text = src[child.start_byte:child.end_byte].decode("utf-8", "replace")
            return text.split("<")[0].strip() or None
    return None


def _find_child_type(node, child_type: str):
    """在直接子节点中按 type 查找。"""
    for c in node.children:
        if c.type == child_type:
            return c
    return None


# ── 调用提取（tree-sitter method_invocation）─────────────────────────────────

def _extract_calls_from_tree(
    node_or_tree, src: bytes, language: str,
    var_types: dict[str, str] | None = None,
) -> list[str]:
    """
    从 AST 节点/树提取方法调用，返回 'ClassName.methodName' 或 'methodName' 列表。
    node_or_tree 可以是整棵树（有 .root_node 属性）或单个 AST 节点（直接遍历）。
    var_types 是 type tracker 映射（字段 + 局部变量），仅 Java 使用。
    """
    calls: list[str] = []
    # 兼容整棵树（旧调用路径）和单个节点（_extract_from_tree 内部优化路径）
    root = node_or_tree.root_node if hasattr(node_or_tree, "root_node") else node_or_tree

    if language == "java":
        _walk_java_calls(root, src, calls, var_types)
    elif language in ("javascript", "typescript"):
        _walk_js_calls(root, src, calls, var_types)

    return list(dict.fromkeys(calls))  # 去重保序


def _walk_java_calls(
    node, src: bytes, out: list[str],
    var_types: dict[str, str] | None = None,
) -> None:
    """
    Java 调用提取。var_types 是 {变量名: 简单类名} 映射（字段 + 局部变量 + 形参），
    用于将 receiver 变量名解析到真实类名：userMapper.selectById → UserMapper.selectById。
    """
    if node.type == "method_invocation":
        name_node = node.child_by_field_name("name")
        obj_node  = node.child_by_field_name("object")
        method = ""
        obj = ""
        if name_node is not None:
            method = src[name_node.start_byte:name_node.end_byte].decode("utf-8", "replace")
        if obj_node is not None:
            obj_text = src[obj_node.start_byte:obj_node.end_byte].decode("utf-8", "replace")
            # 链式：取最后一段作为 receiver 名（a.b.c → c；new Foo() → Foo）
            tail = obj_text.split(".")[-1]
            raw_obj = re.sub(r"[^\w]", "", tail.split("(")[0])
            # type tracker：若 raw_obj 是已知变量名，用其声明类型替换
            if var_types and raw_obj in var_types:
                obj = var_types[raw_obj]
            else:
                obj = raw_obj
        if method:
            out.append(f"{obj}.{method}" if obj else method)
    elif node.type == "object_creation_expression":
        # new Foo(...)  → "Foo" 当成调用（构造器）
        type_node = node.child_by_field_name("type")
        if type_node is not None:
            ctor = src[type_node.start_byte:type_node.end_byte].decode("utf-8", "replace").split(".")[-1]
            ctor = re.sub(r"[^\w]", "", ctor)
            if ctor:
                out.append(ctor)
    for child in node.children:
        _walk_java_calls(child, src, out, var_types)


def _walk_js_calls(
    node, src: bytes, out: list[str],
    var_types: dict[str, str] | None = None,
) -> None:
    """
    JS/TS 调用提取。var_types 是 {变量名: 类型名} 映射（import + 字段 + new 推断），
    用于将 receiver 变量名解析到类型名：userService.save → UserService.save。
    """
    if node.type == "call_expression":
        func = node.child_by_field_name("function")
        if func:
            if func.type == "member_expression":
                obj  = func.child_by_field_name("object")
                prop = func.child_by_field_name("property")
                if obj and prop:
                    obj_text  = src[obj.start_byte:obj.end_byte].decode("utf-8", "replace")
                    prop_text = src[prop.start_byte:prop.end_byte].decode("utf-8", "replace")
                    # this.xxx.method() → 提取 xxx 查类型
                    if obj_text.startswith("this."):
                        field_name = obj_text.split(".")[1]
                        raw_obj = field_name
                    else:
                        raw_obj = obj_text.split(".")[-1]
                    raw_obj = re.sub(r"[^\w]", "", raw_obj.split("(")[0])
                    if var_types and raw_obj in var_types:
                        resolved = var_types[raw_obj]
                    else:
                        resolved = raw_obj
                    out.append(f"{resolved}.{prop_text}")
            elif func.type == "identifier":
                out.append(src[func.start_byte:func.end_byte].decode("utf-8", "replace"))
    elif node.type == "new_expression":
        # new Foo(...) → "Foo" 当成调用（构造器），与 Java 一致
        ctor_name = _extract_new_ctor_name(node, src)
        if ctor_name:
            out.append(ctor_name)
    for child in node.children:
        _walk_js_calls(child, src, out, var_types)


# ── 注解提取 ──────────────────────────────────────────────────────────────────

_ANNOTATION_RE = re.compile(r"@[\w]+(?:\([^)]*\))?")


def _extract_annotations(text: str) -> list[str]:
    lines = text.splitlines()[:10]  # 只看方法头部
    anns = []
    for line in lines:
        anns.extend(_ANNOTATION_RE.findall(line))
    return list(dict.fromkeys(anns))[:5]


# ── Javadoc / 注释提取 ────────────────────────────────────────────────────────

def _extract_leading_comment(text: str) -> str:
    """提取方法上方的 /** ... */ 或 // 注释"""
    lines = text.splitlines()
    comment_lines = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("/**") or stripped.startswith("/*") \
                or stripped.startswith("*") or stripped.startswith("//"):
            # 去掉 */ * // 前缀，只保留文字
            clean = re.sub(r"^[/*\s]+", "", stripped).strip()
            if clean:
                comment_lines.append(clean)
        elif stripped.startswith("@") or stripped == "":
            continue
        else:
            break
    return " ".join(comment_lines[:3])  # 最多 3 行


def _find_comment_start(lines: list[str], method_line_0: int) -> int:
    """
    从方法声明行（0-indexed）向上查找前置 Javadoc / 行注释。

    tree-sitter 的 method_declaration 节点起始于关键字行（如 public …），
    而 Javadoc（/** … */）是父节点的兄弟节点，不在 method_declaration 内。
    此函数向上扫描，找到注释的第一行并返回其 0-indexed 行号。
    若无注释则返回 method_line_0（不改变起始行）。
    """
    idx = method_line_0 - 1
    # 跳过空行和 @注解行（注解在方法签名和 Javadoc 之间很常见）
    while idx >= 0:
        stripped = lines[idx].strip()
        if not stripped or stripped.startswith("@"):
            idx -= 1
        else:
            break
    if idx < 0:
        return method_line_0

    stripped = lines[idx].strip()
    if stripped == "*/" or stripped.endswith("*/"):
        # 块注释（Javadoc）结束符，向上找 /**
        while idx >= 0:
            if lines[idx].strip().startswith("/*"):
                return idx   # 注释的起始行
            idx -= 1
        return method_line_0  # 没找到配对的 /*，放弃
    elif stripped.startswith("//"):
        return idx            # 单行注释，直接返回该行
    return method_line_0


# ── 结构化文档构建 ─────────────────────────────────────────────────────────────

def _build_structured_text(
    raw_code: str,
    file_path: str,
    class_name: str,
    method_name: str,
    signature: str,
    annotations: list[str],
    calls: list[str],
    comment: str,
) -> str:
    """
    将结构化元数据拼在代码前面，一起送入 embed。
    不调用 LLM，纯静态分析。
    """
    parts = []
    parts.append(f"[FILE] {file_path}")
    if class_name:
        parts.append(f"[CLASS] {class_name}")
    if method_name:
        parts.append(f"[METHOD] {method_name}")
    if signature:
        parts.append(f"[SIGNATURE] {signature}")
    if annotations:
        parts.append(f"[ANNOTATIONS] {' '.join(annotations)}")
    if calls:
        parts.append(f"[CALLS] {', '.join(calls[:8])}")  # 最多 8 个调用
    if comment:
        parts.append(f"[COMMENT] {comment}")
    parts.append("")  # 空行分隔
    parts.append(raw_code)
    return "\n".join(parts)


# ── 主接口 ────────────────────────────────────────────────────────────────────

def parse_chunks(source: str, language: str, file_path: str) -> list[dict]:
    """
    返回 chunk 列表，每个 chunk 包含：
      text        - 结构化文档（用于 embed）
      raw_code    - 原始代码（存 snippet 用）
      line_start / line_end
      chunk_type  - method | class | block | sql
      symbol      - method_name
      class_name
      signature
      calls       - 调用的方法名列表（用于构建 Call Graph）
    """
    # MyBatis Mapper XML：每个 <select|insert|update|delete|sql id=...>
    # 当作一个 method 同等对待，后续 indexer 会建 Method 节点 + IMPLEMENTS 边
    if file_path.lower().endswith("mapper.xml"):
        from app.services.mapper_xml_service import parse_mapper_xml
        xml_chunks = parse_mapper_xml(source, file_path)
        if xml_chunks:
            return xml_chunks
        # 非标准 mapper（无 namespace）→ 落回通用 xml sliding-window

    parser = _get_parser(language)
    if parser is not None:
        try:
            tree = parser.parse(source.encode("utf-8"))
            chunks = _extract_from_tree(tree, source, language, file_path)
            # tree-sitter 成功解析：
            #   有 chunks  → 直接返回
            #   空 chunks  → 文件全是 getter/setter 或无方法，不值得索引
            #               不再往下走正则/sliding window，避免产生 block 碎片
            return chunks
        except Exception as e:
            logger.warning("tree-sitter parse error [%s] %s: %s", language, file_path, e)
            # tree-sitter 报错（语法不兼容等），才继续往下兜底

    # 无 tree-sitter 支持的语言，或 tree-sitter 抛异常时的兜底
    regex_chunks = _regex_parse_chunks(source, language, file_path)
    if regex_chunks:
        return regex_chunks

    # sliding window 仅用于无 parser 的语言（xml/yaml/sql 等）
    # tree-sitter 支持的语言（java/js/ts）到这里说明解析失败，返回空跳过
    if parser is not None:
        return []
    return _sliding_window_chunks(source, file_path)


# ── tree-sitter 提取 ───────────────────────────────────────────────────────────

def _extract_from_tree(tree, source: str, language: str, file_path: str) -> list[dict]:
    # tree-sitter 所有偏移量是字节偏移，必须在 bytes 上切片
    # 行号用 source.splitlines()（字符级），代码体也用行号切，两者互不干扰
    source_bytes = source.encode("utf-8")
    lines = source.splitlines()
    target_types = NODE_TYPES.get(language, [])
    chunks: list[dict] = []

    # JS/TS: 文件级 import 类型映射（顶层提取一次）
    js_import_types: dict[str, str] = {}
    if language in ("javascript", "typescript"):
        try:
            js_import_types = _extract_js_imports(tree.root_node, source_bytes)
        except Exception:
            pass

    def walk(node, parent_class: str = "", parent_field_types: dict[str, str] | None = None):
        if "class" in node.type and node.type in target_types:
            # 类名是 identifier 类型的直接子节点，用字节切片保证多字节正确
            for child in node.children:
                if child.type == "identifier":
                    parent_class = source_bytes[child.start_byte:child.end_byte].decode("utf-8", "replace")
                    break
            field_types: dict[str, str] = {}
            if language == "java":
                try:
                    field_types = _extract_field_types(node, source_bytes)
                except Exception:
                    pass
            elif language in ("javascript", "typescript"):
                try:
                    field_types = _extract_js_field_types(node, source_bytes)
                except Exception:
                    pass
            for child in node.children:
                walk(child, parent_class, field_types)
            return

        if node.type in target_types and "class" not in node.type:
            method_line_0  = node.start_point[0]
            line_end       = node.end_point[0] + 1
            # 向上扩展到 Javadoc / 行注释（tree-sitter 的 method_declaration
            # 节点不含前置注释，需要手动往上找）
            comment_line_0 = _find_comment_start(lines, method_line_0)
            line_start     = comment_line_0 + 1        # 1-indexed，含注释
            raw_code       = "\n".join(lines[comment_line_0: line_end])
            symbol         = _extract_symbol(node, source_bytes)

            # 检测是否有 body：interface / abstract 方法没有 body，是合法 symbol，
            # 仍需建索引（service 调用 Mapper.xxx 才有连边目标）。
            has_body = (
                node.child_by_field_name("body") is not None
                if hasattr(node, "child_by_field_name") else
                any(c.type in ("block", "constructor_body") for c in node.children)
            )

            if has_body:
                # 内容质量检查：过滤空方法体 / 孤立括号碎片（如匿名类残留的 }）
                if not _chunk_has_body(raw_code, language):
                    return
                # 跳过无业务价值的简单 getter / setter
                if _is_trivial_accessor(symbol, raw_code):
                    return
            else:
                # 无 body（interface / abstract 方法）：必须有名字才有索引价值
                if not symbol:
                    return

            signature  = _extract_signature(node, source_bytes, language)
            annotations = _extract_annotations(raw_code)
            comment    = _extract_leading_comment(raw_code)

            # 无 body 的 interface / abstract 方法没有调用关系
            calls: list[str] = []
            if has_body:
                try:
                    # 直接在原 AST 节点上走 walk，不重新 parse raw_code（省 ~30% CPU）
                    # 合并字段类型 + 局部变量/形参类型，传给 type tracker
                    var_types: dict[str, str] = {}
                    if language == "java":
                        if parent_field_types:
                            var_types.update(parent_field_types)
                        var_types.update(_extract_local_var_types(node, source_bytes))
                    elif language in ("javascript", "typescript"):
                        var_types.update(js_import_types)
                        if parent_field_types:
                            var_types.update(parent_field_types)
                        # 方法体内 new 表达式推断
                        body = node.child_by_field_name("body")
                        if body:
                            try:
                                local_new: dict[str, str] = {}
                                _extract_new_expr_types(body, source_bytes, local_new)
                                var_types.update(local_new)
                            except Exception:
                                pass
                    calls = _extract_calls_from_tree(
                        node, source_bytes, language, var_types
                    )
                except Exception:
                    pass

            structured = _build_structured_text(
                raw_code    = raw_code,
                file_path   = file_path,
                class_name  = parent_class,
                method_name = symbol,
                signature   = signature,
                annotations = annotations,
                calls       = calls,
                comment     = comment,
            )

            sub = _maybe_split(
                structured, raw_code, line_start, line_end,
                "method", symbol, parent_class, signature, calls
            )
            chunks.extend(sub)
            return

        for child in node.children:
            walk(child, parent_class, parent_field_types)

    walk(tree.root_node)
    if not chunks:
        # tree-sitter 成功解析但无有效方法（全是 getter/setter 或纯常量类）
        # 返回空列表，由 parse_chunks 决定是否兜底，不在此处产生 block 碎片
        return []
    return chunks


def _parse_subtree(code: bytes, language: str):
    """解析局部代码片段，用于提取调用（接受 bytes 以匹配 tree-sitter 字节偏移）"""
    parser = _get_parser(language)
    if parser is None:
        raise RuntimeError("no parser")
    return parser.parse(code)


def _extract_symbol(node, src: bytes) -> str:
    """tree-sitter 用字节偏移，必须从 bytes 而非 str 切片，否则多字节字符导致乱码"""
    for child in node.children:
        if child.type == "identifier":
            return src[child.start_byte:child.end_byte].decode("utf-8", "replace")
    return ""


def _extract_signature(node, src: bytes, language: str) -> str:
    """提取方法签名（不含方法体），从字节流切片避免多字节偏移错误"""
    try:
        text = src[node.start_byte:]
        brace_pos = text.find(b"{")
        if brace_pos == -1:
            return text[:200].decode("utf-8", "replace").strip()
        return text[:brace_pos].decode("utf-8", "replace").strip()[:200]
    except Exception:
        return ""


# ── 正则 fallback ──────────────────────────────────────────────────────────────

def _regex_parse_chunks(source: str, language: str, file_path: str) -> list[dict]:
    lines = source.splitlines()
    n = len(lines)

    if language == "java":
        pattern, sym_group = _JAVA_METHOD_RE, 2
    elif language in ("javascript", "typescript"):
        pattern, sym_group = _JS_FUNC_RE, 2
    else:
        return []

    method_starts: list[tuple[int, str]] = []
    for m in pattern.finditer(source):
        line_idx = source[:m.start()].count("\n")
        groups = m.groups()
        symbol = groups[sym_group - 1] if len(groups) >= sym_group else ""
        if symbol is None:
            symbol = next((g for g in groups[1:] if g and g.strip()
                           and not g.strip().startswith("async")), "")
        method_starts.append((line_idx, symbol or ""))

    if not method_starts:
        return []

    chunks: list[dict] = []
    for i, (start_idx, symbol) in enumerate(method_starts):
        if i + 1 < len(method_starts):
            raw_end = method_starts[i + 1][0]
            end_idx = raw_end - 1
            while end_idx > start_idx and not lines[end_idx].strip():
                end_idx -= 1
        else:
            end_idx = n - 1

        if language in ("java", "kotlin", "csharp", "scala", "javascript", "typescript"):
            end_idx = _find_brace_end(lines, start_idx, end_idx)

        raw_code = "\n".join(lines[start_idx: end_idx + 1])
        if not _chunk_has_body(raw_code, language):
            continue
        if _is_trivial_accessor(symbol, raw_code):
            continue

        annotations = _extract_annotations(raw_code)
        comment     = _extract_leading_comment(raw_code)
        structured  = _build_structured_text(
            raw_code    = raw_code,
            file_path   = file_path,
            class_name  = "",
            method_name = symbol,
            signature   = raw_code.splitlines()[0][:200] if raw_code else "",
            annotations = annotations,
            calls       = [],
            comment     = comment,
        )

        sub = _maybe_split(
            structured, raw_code, start_idx + 1, end_idx + 1,
            "method", symbol, "", "", []
        )
        chunks.extend(sub)

    return chunks


def _find_brace_end(lines: list[str], start: int, max_end: int) -> int:
    depth = 0
    found_open = False
    in_str_d = in_str_s = False
    for i in range(start, min(max_end + 1, len(lines))):
        j = 0
        line = lines[i]
        while j < len(line):
            ch = line[j]
            if not in_str_s and ch == '"' and (j == 0 or line[j-1] != "\\"):
                in_str_d = not in_str_d
            elif not in_str_d and ch == "'" and (j == 0 or line[j-1] != "\\"):
                in_str_s = not in_str_s
            elif not in_str_d and not in_str_s:
                if ch == "{":
                    depth += 1
                    found_open = True
                elif ch == "}":
                    depth -= 1
                    if found_open and depth == 0:
                        return i
            j += 1
    return max_end


def _chunk_has_body(text: str, language: str) -> bool:
    stripped = text.strip()
    lines = [l.strip() for l in stripped.splitlines() if l.strip()]
    if not lines:
        return False
    if language == "python":
        return len(lines) >= 2
    if "{" not in stripped or "}" not in stripped:
        return False
    non_trivial = [l for l in lines
                   if not l.startswith("*") and not l.startswith("//")
                   and not l.startswith("/*") and not l.startswith("@")
                   and l not in ("{", "}", "")]
    return len(non_trivial) >= 2


# ── Getter / Setter 过滤 ───────────────────────────────────────────────────────
# get/set 通常无业务价值；但 is/has 经常是工具方法（如 StringUtils.isEmpty
# (Object[]) ），不能仅凭名字 + 行数粗暴过滤。新策略：方法体必须是"字面字段读写"
# 才算 trivial，含任何方法调用、运算符、条件都保留索引。

_GETTER_PREFIX_RE = re.compile(r'^(?:get|set)[A-Z]')

# 字面字段读：return foo;   return this.foo;
_TRIVIAL_GET_RE = re.compile(r"^\s*return\s+(?:this\.)?\w+\s*;?\s*$")
# 字面字段写：this.foo = foo;   foo = bar;
_TRIVIAL_SET_RE = re.compile(r"^\s*(?:this\.)?\w+\s*=\s*\w+\s*;?\s*$")


def _is_trivial_accessor(symbol: str, raw_code: str) -> bool:
    """
    判定真正"无业务价值"的 getter / setter，跳过以降低索引噪音。

    判定要求（全部满足）：
      1. 方法名以 get / set 开头（不再误伤 is / has —— ruoyi StringUtils.isEmpty
         这种短小工具方法是用户真正想搜的对象）
      2. 去掉签名、注释、注解、括号后，剩余 body 恰好 1 行
      3. 该行是字面字段读/写（return foo;  /  this.foo = foo;），
         任何方法调用、运算符、判断都不算 trivial
    """
    if not symbol or not _GETTER_PREFIX_RE.match(symbol):
        return False

    # 提取方法体：第一个 { 到最后一个 } 之间
    first_brace = raw_code.find("{")
    last_brace  = raw_code.rfind("}")
    if first_brace < 0 or last_brace <= first_brace:
        return False
    body = raw_code[first_brace + 1: last_brace]

    body_lines = [
        l.strip()
        for l in body.splitlines()
        if l.strip()
        and l.strip() not in ("{", "}")
        and not l.strip().startswith(("/", "*", "@"))
    ]
    if len(body_lines) != 1:
        return False

    line = body_lines[0]
    return bool(_TRIVIAL_GET_RE.match(line) or _TRIVIAL_SET_RE.match(line))


# ── maybe_split ───────────────────────────────────────────────────────────────

def _meta_header_from_structured(structured: str) -> str:
    """
    从结构化文本中提取元数据头（空行之前的 [FILE]/[CLASS]/[METHOD] 等行）。
    用于超长方法分窗时，确保每个分窗都携带语义标识，不再是"裸代码"。
    """
    lines = structured.split("\n")
    header: list[str] = []
    for line in lines:
        if not line.strip():
            break
        header.append(line)
    return "\n".join(header)


def _maybe_split(
    structured: str, raw_code: str,
    line_start: int, line_end: int,
    chunk_type: str, symbol: str,
    class_name: str, signature: str,
    calls: list[str],
) -> list[dict]:
    if _count_tokens(structured) <= settings.CHUNK_MAX_TOKENS:
        return [{
            "text":       structured,
            "raw_code":   raw_code[:3000],
            "line_start": line_start,
            "line_end":   line_end,
            # method_line_* 表示完整方法范围；单 chunk 时与 chunk 自己的 line_* 一致
            "method_line_start": line_start,
            "method_line_end":   line_end,
            "chunk_type": chunk_type,
            "symbol":     symbol,
            "class_name": class_name,
            "signature":  signature,
            "calls":      calls,
            # 单 chunk 即代表该方法
            "is_primary": True,
        }]

    # 超长方法：提取元数据头，每个分窗都携带，避免大方法的 chunk 成为裸代码
    meta_header = _meta_header_from_structured(structured)
    header_tokens = _count_tokens(meta_header)
    # 代码窗口大小 = 总限制 - 头部占用 - 2（空行分隔符）
    code_window_tokens = max(settings.CHUNK_MAX_TOKENS - header_tokens - 2, 100)

    windows = _sliding_window_chunks(
        raw_code, "", chunk_type, base_line=line_start,
        symbol=symbol, class_name=class_name, signature=signature, calls=calls,
        max_tokens=code_window_tokens,
    )
    # 只有第一窗是"该方法的代表"（写 symbols 表、建 Method 节点、参与 call graph 连边）；
    # 后续窗口共享同一 symbol_id，仅作为额外的 Qdrant 候选，避免：
    #   1. 同名方法歧义 → 连边全丢
    #   2. 一个长方法占多个 top-k 名额
    #
    # method_line_* 在所有分窗上都标完整方法范围；
    # chunk 自己的 line_start/line_end 仍是分窗范围（snippet 行号要准）。
    # 这样：
    #   - SQLite symbols 表用 method_line_*，光标在方法任何一行都能命中 primary
    #   - Qdrant payload 用 chunk-local line_*，搜索结果显示精确分窗位置
    for i, w in enumerate(windows):
        w["text"] = meta_header + "\n\n" + w["text"]
        w["is_primary"] = (i == 0)
        w["method_line_start"] = line_start
        w["method_line_end"]   = line_end
    return windows


# ── Sliding Window ─────────────────────────────────────────────────────────────

def _sliding_window_chunks(
    source: str, file_path: str,
    chunk_type: str = "block",
    base_line: int = 1,
    symbol: str = "",
    class_name: str = "",
    signature: str = "",
    calls: list[str] | None = None,
    max_tokens: int | None = None,   # 由 _maybe_split 传入调整后的窗口大小
) -> list[dict]:
    lines = source.splitlines()
    _max   = max_tokens if max_tokens is not None else settings.CHUNK_MAX_TOKENS
    overlap = settings.CHUNK_OVERLAP_TOKENS
    chunks  = []
    start_idx = 0

    while start_idx < len(lines):
        current_lines: list[str] = []
        current_tokens = 0
        end_idx = start_idx

        while end_idx < len(lines):
            lt = _count_tokens(lines[end_idx])
            if current_tokens + lt > _max and current_lines:
                break
            current_lines.append(lines[end_idx])
            current_tokens += lt
            end_idx += 1

        if not current_lines:
            current_lines = [lines[start_idx]]
            end_idx = start_idx + 1

        text = "\n".join(current_lines)

        # 跳过只含括号 / 注释的无意义尾窗（超长方法分窗后末尾常见 }、} } 碎片）
        _meaningful = [
            l for l in current_lines
            if l.strip()
            and l.strip() not in ("{", "}", "*/", "*")
            and not l.strip().startswith(("/**", "/*", "* ", "//", "@"))
        ]
        if not _meaningful:
            overlap_lines = max(1, overlap // 10)
            start_idx = max(end_idx - overlap_lines, start_idx + 1)
            continue

        ls = base_line + start_idx
        le = base_line + end_idx - 1
        chunks.append({
            "text":       text,
            "raw_code":   text[:3000],
            "line_start": ls,
            "line_end":   le,
            # 默认 method 范围与 chunk 自身一致；_maybe_split 切窗时会覆盖为完整方法范围
            "method_line_start": ls,
            "method_line_end":   le,
            "chunk_type": chunk_type,
            "symbol":     symbol,
            "class_name": class_name,
            "signature":  signature,
            "calls":      calls or [],
            # _maybe_split 调用时会覆写此字段；直接调用（xml/yaml fallback）时
            # 每个窗都是独立 doc，全部 primary
            "is_primary": True,
        })

        overlap_lines = max(1, overlap // 10)
        start_idx = max(end_idx - overlap_lines, start_idx + 1)

    return chunks
