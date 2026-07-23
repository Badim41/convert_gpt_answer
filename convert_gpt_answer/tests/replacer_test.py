import unittest
import importlib.util
import os
import sys

# Динамический загрузчик для импорта основного скрипта replacer
# Он ищет файл в родительской директории по ключевой функции `def find_anchors_match`,
# чтобы тесты работали гарантированно, даже если файл называется main.py, replacer.py или code.py.
def load_replacer_module():
    parent_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
    for f in os.listdir(parent_dir):
        if f.endswith('.py') and f != '__init__.py':
            path = os.path.join(parent_dir, f)
            try:
                with open(path, 'r', encoding='utf-8') as fp:
                    if 'def find_anchors_match' in fp.read():
                        spec = importlib.util.spec_from_file_location("replacer_module", path)
                        module = importlib.util.module_from_spec(spec)
                        spec.loader.exec_module(module)
                        return module
            except Exception:
                pass
    raise ImportError("Не удалось найти основной скрипт replacer в родительской директории.")

replacer = load_replacer_module()


class TestReplacerAlgorithms(unittest.TestCase):

    def test_fuzzy_match_dynamic_threshold(self):
        """Тест 1: Нечеткий поиск (Fuzzy Match) на коротком блоке (<4 строк) с опечаткой/галлюцинацией."""
        file_lines = ['const PORT = 8080;', 'const HOST = "127.0.0.1";', 'start_server(PORT, HOST);']
        search_lines = ['const PORT = 8080;', 'const HOST = "localhost";', 'start_server(PORT, HOST);']
        candidates = replacer.find_fuzzy_block(search_lines, file_lines, threshold=0.85)
        self.assertTrue(len(candidates) > 0)
        self.assertEqual(candidates[0]['start'], 0)
        self.assertEqual(candidates[0]['end'], 3)
        self.assertTrue(candidates[0]['ratio'] > 0.65)

    def test_anchor_match_missing_middle(self):
        """Тест 2: Якорный поиск при сильной галлюцинации в середине блока."""
        file_lines = ["def complex_func():", "    setup()", "    a = 1", "    b = 2", "    c = 3", "    cleanup()", "    return True"]
        search_lines = ["def complex_func():", "    setup()", "    a = 999", "    b = 888", "    cleanup()", "    return True"]
        candidates = replacer.find_anchors_match(search_lines, file_lines)
        self.assertTrue(len(candidates) > 0)
        self.assertEqual(candidates[0]['start'], 0)
        self.assertEqual(candidates[0]['end'], 7)

    def test_anchor_match_duplicate_resolution(self):
        """Тест 3: Разрешение неоднозначностей с якорями, выбор ближайшего."""
        file_lines = ["def other_func():", "    setup()", "    cleanup()", "    return True", "def complex_func():", "    setup()", "    a = 1", "    cleanup()", "    return True"]
        search_lines = ["def complex_func():", "    setup()", "    a = 999", "    cleanup()", "    return True"]
        candidates = replacer.find_anchors_match(search_lines, file_lines)
        self.assertTrue(len(candidates) > 0)
        self.assertEqual(candidates[0]['start'], 4)
        self.assertEqual(candidates[0]['end'], 9)

    def test_extract_filenames_windows_path_and_scopes(self):
        """Тест 4: Проверка регулярного выражения имен файлов с Windows-путями и scopes."""
        lines = ["Я нашел баг. Исправь его в src\\controllers\\user.controller.js", "А также обнови @components/Button.tsx", "<<<<"]
        filenames = replacer.extract_filenames_from_prompt(lines)
        self.assertIn("user.controller.js", filenames)
        self.assertIn("Button.tsx", filenames)
        self.assertEqual(len(filenames), 2)

    def test_fuzzy_match_fast_filter(self):
        """Тест 5: Проверка быстрого фильтра слов `s_words.intersection(w_words)`."""
        file_lines = ["// Описание функции", "let total_sum = 0;", "return total_sum;"]
        search_lines = ["// Описание функции расчет", "let total_sum = 0;", "return total_sum;"]
        candidates = replacer.find_fuzzy_block(search_lines, file_lines, threshold=0.70)
        self.assertTrue(len(candidates) > 0)
        self.assertEqual(candidates[0]['start'], 0)

    def test_anchor_match_indentation_insensitive(self):
        """Тест 6: Якорный поиск должен быть нечувствителен к изменениям отступов."""
        file_lines = ["    def func():", "        a = 1", "        b = 2", "        return a + b", "        # end"]
        search_lines = ["def func():", "a = 1", "b = 999  # hallucination", "c = 3", "return a + b", "# end"]
        candidates = replacer.find_anchors_match(search_lines, file_lines)
        self.assertTrue(len(candidates) > 0)
        self.assertEqual(candidates[0]['start'], 0)
        self.assertEqual(candidates[0]['end'], 5)

    def test_anchor_match_too_large_distance_rejection(self):
        """Тест 7: Якорный поиск должен отклонять блоки, если реальное расстояние между якорями больше ожидаемого (удалено слишком много)."""
        file_lines = ["top1", "top2"] + ["fill"] * 20 + ["bot1", "bot2"]
        search_lines = ["top1", "top2", "hallucination", "bot1", "bot2"]
        candidates = replacer.find_anchors_match(search_lines, file_lines)
        self.assertEqual(len(candidates), 0)

    def test_fuzzy_match_file_smaller_than_search(self):
        """Тест 8: Нечеткий поиск, если искомый сгаллюцинированный блок больше самого файла целиком."""
        file_lines = ["line1", "line2"]
        search_lines = ["line1", "line2", "line3"]
        candidates = replacer.find_fuzzy_block(search_lines, file_lines, threshold=0.7)
        self.assertTrue(len(candidates) > 0)
        self.assertEqual(candidates[0]['end'], 2)

    def test_extract_powershell_commands_markdown(self):
        """Тест 9: Извлечение многострочных PS-команд из markdown-блоков."""
        lines = ["Some text\n", "```powershell\n", "Write-Host 'Hello'\n", "```\n"]
        cmds = replacer.extract_powershell_commands(lines)
        self.assertEqual(len(cmds), 1)
        self.assertEqual(cmds[0], "Write-Host 'Hello'\n")

    def test_extract_powershell_commands_here_string(self):
        """Тест 10: Извлечение PS-команд для создания файлов через Here-String."""
        lines = ["$content = @'\n", "def foo():\n", "    pass\n", "'@\n", "$content | Out-File -FilePath 'test.py'\n"]
        cmds = replacer.extract_powershell_commands(lines)
        self.assertEqual(len(cmds), 1)
        self.assertIn("def foo():\n", cmds[0])

    def test_compute_mismatch_stats_calculation(self):
        """Тест 11: Убедиться в корректности расчета статистики несоответствий."""
        search_lines = ["a", "b", "c"]
        cand_lines = ["a", "x", "c"]
        stats = replacer.compute_mismatch_stats(search_lines, cand_lines)
        self.assertEqual(stats['mismatched_lines'], 1)
        self.assertEqual(stats['mismatched_chars'], 1)

    def test_is_binary_file_detection(self):
        """Тест 12: Игнорирование бинарных файлов (в том числе SQLite) для предотвращения крашей."""
        import tempfile
        with tempfile.NamedTemporaryFile(delete=False) as f:
            f.write(b'SQLite format 3\x00')
            temp_name = f.name
        try:
            self.assertTrue(replacer.is_binary_file(temp_name))
        finally:
            os.remove(temp_name)

    def test_find_matches_exact_match(self):
        """Тест 13: Точный поиск должен корректно игнорировать незначащие пробелы."""
        file_lines = ["  a  ", "b", "c"]
        search_lines = ["a", "  b", "c  "]
        matches = replacer.find_matches(search_lines, file_lines)
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0], (0, 3))

    def test_fuzzy_match_ignores_leading_trailing_empty_lines(self):
        """Тест 14: Нечеткий поиск не должен сбиваться из-за пустых строк по краям."""
        file_lines = ["a", "b", "c"]
        search_lines = ["   ", "a", "b", "c", ""]
        candidates = replacer.find_fuzzy_block(search_lines, file_lines, threshold=0.99)
        self.assertTrue(len(candidates) > 0)
        self.assertEqual(candidates[0]['start'], 0)
        self.assertEqual(candidates[0]['end'], 3)

    def test_fuzzy_match_crlf_vs_lf(self):
        """Тест 15: Кроссплатформенность, переводы кареток CRLF и LF считаются равными."""
        file_lines = ["a\r\n", "b\r\n", "c\r\n"]
        search_lines = ["a\n", "b\n", "c\n"]
        candidates = replacer.find_fuzzy_block(search_lines, file_lines, threshold=0.99)
        self.assertTrue(len(candidates) > 0)

    def test_anchor_match_duplicate_top_anchors_returns_multiple(self):
        """Тест 16: При наличии дубликатов верхнего якоря, алгоритм обязан вернуть все найденные области для проверки."""
        file_lines = ["top1", "top2", "mid1", "bot1", "bot2", "top1", "top2", "mid2", "bot1", "bot2"]
        search_lines = ["top1", "top2", "hallucination", "bot1", "bot2"]
        candidates = replacer.find_anchors_match(search_lines, file_lines)
        self.assertEqual(len(candidates), 2)

    def test_anchor_match_with_empty_lines_in_middle(self):
        """Тест 17: Якорный поиск нормально проглатывает пустые строки внутри блока."""
        file_lines = ["top1", "top2", "", "", "bot1", "bot2"]
        search_lines = ["top1", "top2", "  ", "hallucination", "bot1", "bot2"]
        candidates = replacer.find_anchors_match(search_lines, file_lines)
        self.assertTrue(len(candidates) > 0)
        self.assertEqual(candidates[0]['start'], 0)
        self.assertEqual(candidates[0]['end'], 6)

    def test_fuzzy_match_rejects_totally_different_code(self):
        """Тест 18: Ошибочные, абсолютно чужеродные блоки отсекаются быстрым фильтром."""
        file_lines = ["apple", "banana", "cherry"]
        search_lines = ["dog", "elephant", "fox", "wolf"]
        candidates = replacer.find_fuzzy_block(search_lines, file_lines, threshold=0.7)
        self.assertEqual(len(candidates), 0)

    def test_extract_filenames_multiple_extensions(self):
        """Тест 19: Извлечение имен файлов с несколькими точками (например .test.ts)."""
        lines = ["Please fix user.controller.test.ts and utils.min.js!"]
        files = replacer.extract_filenames_from_prompt(lines)
        self.assertIn("user.controller.test.ts", files)
        self.assertIn("utils.min.js", files)

    def test_extract_filenames_ignores_invalid(self):
        """Тест 20: Регулярка не должна извлекать слова с точкой на конце как имена файлов."""
        lines = ["This is not a file.txt, well maybe file.txt is, but ending. with dot is not."]
        files = replacer.extract_filenames_from_prompt(lines)
        self.assertIn("file.txt", files)
        self.assertNotIn("ending.", files)

    def test_fuzzy_match_empty_search(self):
        """Тест 21: Попытка найти пустой блок (или состоящий из пробелов) возвращает пустоту, а не ложные срабатывания."""
        candidates = replacer.find_fuzzy_block(["   ", ""], ["a", "b"], threshold=0.7)
        self.assertEqual(len(candidates), 0)

    def test_anchor_match_short_search(self):
        """Тест 22: Якорный поиск блокируется на слишком коротких блоках (<3 строк), чтобы избежать коллизий."""
        candidates = replacer.find_anchors_match(["a", "b"], ["a", "b", "c"])
        self.assertEqual(len(candidates), 0)

    def test_find_fuzzy_block_hallucinated_trailing_comments(self):
        """Тест 23: Нечеткий поиск находит блок, даже если LLM приписала свои комментарии в хвост каждой строки."""
        file_lines = ["a = 1", "b = 2", "c = 3", "d = 4"]
        search_lines = ["a = 1 # init a", "b = 2 # init b", "c = 3", "d = 4"]
        candidates = replacer.find_fuzzy_block(search_lines, file_lines, threshold=0.6)
        self.assertTrue(len(candidates) > 0)

    def test_extract_filenames_url_exclusion(self):
        """Тест 24: Парсер способен безопасно извлекать имена файлов даже из сырых URL."""
        lines = ["Check out https://github.com/repo/main.py"]
        files = replacer.extract_filenames_from_prompt(lines)
        self.assertIn("main.py", files)

    def test_anchor_match_empty_search(self):
        """Тест 25: Якорный поиск не падает при отправке полностью пустого запроса."""
        candidates = replacer.find_anchors_match([], ["a", "b", "c"])
        self.assertEqual(len(candidates), 0)


if __name__ == '__main__':
    unittest.main(verbosity=2)
