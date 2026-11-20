# -*- coding: utf-8 -*-
"""分层边界守护：包之间的 import 方向不许反向。

判据与 README「后端分层」一致：

- ``docx_common`` 是地板，只依赖 ``document_contract``，任何层都可以依赖它；
- ``recognition`` 只依赖 ``docx_common``：它回答"这一段是什么"，不反向依赖解析/检测；
- ``detection`` 不 import ``recognition``：段落类型一律走 ``document`` 里的 ``kind``，
  剥模板括注走 ``backend/docx_common/notes.py``（这两个工具以前把检测层和识别层绑在一起）；
- ``write_back`` 不 import ``parser``：写回只认数据块，不重做解析；
- 任何模块都不 import ``api``：``api`` 是顶层编排，被 import 就会形成环。

这些约束以前只写在 README 里，改动一次就悄悄失效——这里用 AST 把真实 import
读出来断言，注释和字符串里的包名不算。
"""
import ast
import io
import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# 后端代码都在 backend/ 包下：这里按"包内名字"给断言，所以去掉 backend. 前缀。
BACKEND = "backend"
PACKAGES = tuple("backend/%s" % name for name in
                 ("detection", "fix", "parser", "rag", "recognition",
                  "write_back", "docx_common"))
ROOT_MODULES = tuple("backend/%s" % name for name in
                     ("api", "college_registry", "config", "document_contract",
                      "check_jobs"))
FIRST_PARTY = {name.split("/")[-1] for name in PACKAGES + ROOT_MODULES}


def _read(path):
    with io.open(path, encoding="utf-8") as handle:
        return handle.read()


def _iter_sources(relative):
    """产出 (文件路径, 源码) —— relative 既可以是目录也可以是 .py 文件名。"""
    path = os.path.join(ROOT, relative)
    if os.path.isfile(path):
        yield path, _read(path)
        return
    for dirpath, dirnames, filenames in os.walk(path):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        for name in filenames:
            if name.endswith(".py"):
                full = os.path.join(dirpath, name)
                yield full, _read(full)


def _inside_backend(name):
    """把 ``backend.parser.x`` 归成 ``parser``；不是后端模块就返回 None。"""
    parts = name.split(".")
    if len(parts) < 2 or parts[0] != BACKEND:
        return None
    return parts[1]


def _imports_of(relative):
    """该目录/文件 import 到的后端模块名（相对 import 不算，属包内）。"""
    found = set()
    for _, source in _iter_sources(relative):
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module]
            else:
                continue
            for name in names:
                inside = _inside_backend(name)
                if inside:
                    found.add(inside)
    return found & FIRST_PARTY


class LayerBoundaryTest(unittest.TestCase):
    def test_docx_common_depends_only_on_the_contract(self):
        """地板不许挂业务包，否则每个依赖它的层都被拖进业务配置。"""
        self.assertEqual(_imports_of("backend/docx_common"), {"document_contract"})

    def test_recognition_depends_only_on_floors(self):
        self.assertEqual(_imports_of("backend/recognition"),
                         {"college_registry", "docx_common"})

    def test_detection_does_not_import_recognition(self):
        self.assertNotIn("recognition", _imports_of("backend/detection"))

    def test_write_back_does_not_import_parser(self):
        self.assertNotIn("parser", _imports_of("backend/write_back"))

    def test_no_module_imports_api(self):
        for relative in PACKAGES:
            self.assertNotIn("api", _imports_of(relative), relative)


class NoteHelperDefinitionTest(unittest.TestCase):
    """剥模板括注的两个工具只允许有一份定义。"""

    NAMES = ("strip_trailing_note", "style_note_start")

    def _definitions(self):
        found = {}
        for relative in list(PACKAGES) + list(ROOT_MODULES):
            for path, source in _iter_sources(relative):
                for node in ast.walk(ast.parse(source)):
                    if isinstance(node, ast.FunctionDef) and node.name in self.NAMES:
                        found.setdefault(node.name, []).append(
                            os.path.relpath(path, ROOT).replace("\\", "/"))
        return found

    def test_defined_once_in_docx_common_notes(self):
        definitions = self._definitions()
        for name in self.NAMES:
            self.assertEqual(
                definitions.get(name), ["backend/docx_common/notes.py"],
                "%s 的定义不止一处：%s" % (name, definitions.get(name)),
            )


if __name__ == "__main__":
    unittest.main()
