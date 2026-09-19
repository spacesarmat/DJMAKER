"""Экран FAQ: навигация и рендер карточек вопрос-ответ."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import Mock

import flet as ft

from djmaker.ui.faq_controls import FAQ_TOPICS, FaqController


class FaqScreenTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = SimpleNamespace()
        self.app._set_navigation_index = Mock()
        self.app._replace_content = Mock()
        self.app._surface_card = lambda content, **kwargs: ft.Container(content=content)
        self.controller = FaqController(self.app)

    def test_show_faq_sets_navigation_index_to_new_destination(self) -> None:
        self.controller.show_faq()

        self.app._set_navigation_index.assert_called_once_with(8)

    def test_show_faq_replaces_content_with_one_card_per_topic(self) -> None:
        self.controller.show_faq()

        self.app._replace_content.assert_called_once()
        args = self.app._replace_content.call_args.args
        faq_list = args[2]
        self.assertEqual(len(FAQ_TOPICS), len(faq_list.controls))

    def test_every_topic_has_at_least_one_question(self) -> None:
        for _icon, title, entries in FAQ_TOPICS:
            with self.subTest(topic=title):
                self.assertGreaterEqual(len(entries), 1)
                for question, answer in entries:
                    self.assertTrue(question.strip())
                    self.assertTrue(answer.strip())

    def test_topic_card_renders_title_and_all_questions(self) -> None:
        icon, title, entries = FAQ_TOPICS[0]
        card = self.controller.topic_card(icon, title, entries)

        rendered = repr(card)
        self.assertIn(title, rendered)
        for question, _answer in entries:
            self.assertIn(question, rendered)


if __name__ == "__main__":
    unittest.main()
