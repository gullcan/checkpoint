"""Behavior checks use synthetic data, temporary files and mocked APIs only."""

import copy
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

import llm
import main as cli
import planning
import reminders
import storage
import telegram_bot as bot
from workflow import daily_summary, record_checkpoint, update_task_context


def task(task_id="task0001", **changes):
    result = {
        "id": task_id, "title": "CV güncelle", "importance": 4,
        "estimated_minutes": 60, "cognitive_load": "medium", "deadline": None,
        "desired_outcome": "Başvuruya hazır CV", "context": "Projeler bölümü eksik",
    }
    result.update(changes)
    return result


class PlanningTests(unittest.TestCase):
    def test_deadline_and_energy_rules(self):
        today = date(2026, 9, 25)
        for offset, bonus in ((-1, 4), (0, 4), (1, 3), (2, 1), (3, 1), (4, 0)):
            for load, penalty in (("low", 0), ("medium", 1), ("high", 3)):
                with self.subTest(offset=offset, load=load):
                    item = task(deadline=(today + timedelta(days=offset)).isoformat(), cognitive_load=load)
                    self.assertEqual(planning.calculate_priority(item, 2, today)["score"], 8 + bonus - penalty)
                    self.assertEqual(planning.calculate_priority(item, 3, today)["score"], 8 + bonus)
        self.assertEqual(planning.calculate_priority(task(), 3, today)["score"], 8)

    def test_long_tasks_get_short_sessions_and_total_never_exceeds_time(self):
        items = [task(f"task000{n}") for n in range(1, 4)]
        for minutes, expected in ((0, []), (5, [5]), (25, [15, 10]), (60, [15, 15])):
            with self.subTest(minutes=minutes):
                active, allocated = planning.select_active_tasks(items, minutes, 3, date.today())
                self.assertEqual(list(allocated.values()), expected)
                self.assertLessEqual(sum(allocated.values()), minutes)
                self.assertLessEqual(len(active), 2)

    def test_ties_preserve_order_and_ineligible_tasks_are_skipped(self):
        items = [task("blocked1", blocked=True), task("archived", archived=True),
                 task("finished", completed=True), task("second00"), task("first000")]
        active, _ = planning.select_active_tasks(items, 30, 3, date.today())
        self.assertEqual([item["id"] for item in active], ["second00", "first000"])

    def test_short_task_does_not_reserve_fifteen_minutes(self):
        active, allocated = planning.select_active_tasks([task(estimated_minutes=3)], 10, 3, date.today())
        self.assertEqual(allocated[active[0]["id"]], 3)


class WorkflowTests(unittest.TestCase):
    def test_feedback_transitions_keep_history_and_do_not_finish_parent(self):
        for feedback in ("done", "continue", "blocked"):
            with self.subTest(feedback=feedback):
                item, day = task(), {"remaining_minutes": 20}
                record_checkpoint(item, day, "Bir madde yaz", feedback, 15, 5, "Açıklama")
                self.assertEqual(day["remaining_minutes"], 15)
                self.assertEqual(item["blocked"], feedback == "blocked")
                self.assertEqual(item["next_action"], None if feedback == "done" else "Bir madde yaz")
                self.assertFalse(item.get("completed", False))
                self.assertEqual(item["checkpoints"][0]["action"], "Bir madde yaz")
                self.assertTrue(storage.is_valid_task(item))

    def test_invalid_feedback_leaves_data_unchanged(self):
        for feedback, spent, note in (("done", 5, ""), ("blocked", 5, None),
                                      ("continue", -1, None), ("unknown", 5, None)):
            with self.subTest(feedback=feedback, spent=spent):
                item, day = task(), {"remaining_minutes": 20}
                before = copy.deepcopy((item, day))
                with self.assertRaises(ValueError):
                    record_checkpoint(item, day, "Adım", feedback, 15, spent, note)
                self.assertEqual((item, day), before)

    def test_reported_overrun_is_preserved_but_remaining_time_stops_at_zero(self):
        item, day = task(), {"remaining_minutes": 5}
        record_checkpoint(item, day, "Adım", "continue", 5, 12)
        self.assertEqual(day["remaining_minutes"], 0)
        self.assertEqual(item["checkpoints"][0]["spent_minutes"], 12)

    def test_context_change_clears_action_but_keeps_checkpoints(self):
        item = task()
        record_checkpoint(item, {"remaining_minutes": 20}, "Adım", "continue", 15, 5)
        history = copy.deepcopy(item["checkpoints"])
        self.assertFalse(update_task_context(item, item["desired_outcome"], None))
        self.assertEqual(item["next_action"], "Adım")
        self.assertTrue(update_task_context(item, None, "Yeni bağlam"))
        self.assertIsNone(item["next_action"])
        self.assertEqual(item["checkpoints"], history)

    def test_summary_uses_local_date_and_identifies_old_missing_durations(self):
        now = datetime.now().astimezone().replace(hour=12)
        item, day = task(), {"remaining_minutes": 40}
        record_checkpoint(item, day, "Eski", "done", 15, 5, "Çıktı", now=now - timedelta(days=1))
        record_checkpoint(item, day, "Yeni", "done", 15, 7, "Çıktı", now=now)
        record_checkpoint(item, day, "Devam", "continue", 15, 3, now=now)
        del item["checkpoints"][-1]["spent_minutes"]
        summary = daily_summary([item], now.date())
        self.assertEqual(summary["counts"], {"done": 1, "continue": 1, "blocked": 0})
        self.assertEqual(summary["spent_minutes"], 7)
        self.assertEqual(summary["missing_duration"], 1)


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "state.json"

    def test_missing_file_and_round_trip(self):
        self.assertEqual(storage.load_state(self.path), {"tasks": [], "day": None})
        item, day = task(), {"date": date.today().isoformat(), "remaining_minutes": 40, "energy": 3}
        record_checkpoint(item, day, "Bir madde yaz", "continue", 15, 5)
        state = {"tasks": [item], "day": day}
        storage.save_state(state, self.path)
        self.assertEqual(storage.load_state(self.path), state)
        self.assertFalse(self.path.with_suffix(".tmp").exists())

    def test_invalid_records_are_rejected_without_overwriting(self):
        invalid = [b"{broken", b"\xff", json.dumps({"tasks": [None]}).encode(),
                   json.dumps({"tasks": [task(), task()]}).encode(),
                   json.dumps({"tasks": [], "day": {"date": "wrong"}}).encode()]
        for data in invalid:
            with self.subTest(data=data[:30]):
                self.path.write_bytes(data)
                with self.assertRaises(SystemExit):
                    storage.load_state(self.path)
                self.assertEqual(self.path.read_bytes(), data)

    def test_task_type_and_optional_fields(self):
        for value in (None, 3, "bad", [], task(importance=True), task(telegram_status_update_id="bad")):
            with self.subTest(value=value):
                self.assertFalse(storage.is_valid_task(value))

    def test_legacy_tasks_get_an_id_without_rewriting_on_read(self):
        item = task()
        del item["id"]
        data = json.dumps({"tasks": [item], "day": None})
        self.path.write_text(data, encoding="utf-8")
        self.assertTrue(storage.load_state(self.path)["tasks"][0]["id"])
        self.assertEqual(self.path.read_text(encoding="utf-8"), data)

    def test_failed_replace_preserves_previous_state(self):
        storage.save_state({"tasks": [], "day": None}, self.path)
        before = self.path.read_bytes()
        with patch.object(Path, "replace", side_effect=OSError("simulated disk failure")):
            with self.assertRaises(SystemExit):
                storage.save_state({"tasks": [task()], "day": None}, self.path)
        self.assertEqual(self.path.read_bytes(), before)

    def test_malformed_reminder_and_wrong_owner_are_rejected(self):
        state = {"tasks": []}
        runtime = storage.get_runtime(state, 123)
        with self.assertRaises(SystemExit):
            storage.get_runtime(state, 456)
        for value in (None, [], {"enabled": True, "time": 9}, {"enabled": "yes"}):
            runtime["reminder"] = value
            with self.subTest(value=value), self.assertRaises(SystemExit):
                storage.get_runtime(state, 123)


class TelegramTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        patcher = patch.object(storage, "STATE_PATH", Path(self.directory.name) / "state.json")
        patcher.start()
        self.addCleanup(patcher.stop)
        self.state = {"tasks": [task(), task("task0002", importance=3)],
                      "day": {"date": date.today().isoformat(), "remaining_minutes": 40, "energy": 3}}
        self.session = storage.get_runtime(self.state, 123)["session"]

    def prepare(self, number=1):
        bot.build_plan_message("/plan", self.session, self.state)
        bot.select_work_message(f"/sec {number} elle", self.session, self.state)
        bot.set_action_message("/eylem Bir madde yaz", self.session)

    def test_default_plan_uses_saved_day_without_spending_time(self):
        bot.build_plan_message("/plan", self.session, self.state)
        self.assertEqual(self.session["plan"]["energy"], 3)
        self.assertEqual(self.state["day"]["remaining_minutes"], 40)
        self.assertEqual(len(self.session["plan"]["task_ids"]), 2)

    def test_zero_or_invalid_plan_keeps_existing_work(self):
        self.prepare()
        before = copy.deepcopy(self.session)
        for command in ("/plan 0 3", "/plan 99 3", "/plan -1 3", "/plan 20 9", "/plan x 3"):
            bot.build_plan_message(command, self.session, self.state)
            self.assertEqual(self.session, before)

    def test_second_task_feedback_and_turkish_aliases(self):
        for word, feedback, note in (("tamam", "done", "Çıktı"), ("devam", "continue", ""), ("engel", "blocked", "Dosya yok")):
            with self.subTest(word=word):
                self.state["tasks"][1]["blocked"] = False
                self.prepare(2)
                bot.record_feedback_message(f"/kaydet {word} 5 | {note}", self.session, len(feedback), self.state)
                self.assertNotIn("checkpoints", self.state["tasks"][0])
                self.assertEqual(self.state["tasks"][1]["checkpoints"][-1]["feedback"], feedback)
                self.assertFalse(self.session)

    def test_api_failure_preserves_selection_and_manual_action_can_continue(self):
        bot.build_plan_message("/plan", self.session, self.state)
        with patch.object(bot, "generate_next_action", side_effect=ValueError("offline")):
            bot.select_work_message("/sec 1", self.session, self.state)
        self.assertEqual(self.session["work"]["task_id"], "task0001")
        self.assertIsNone(self.session["work"]["action"])
        bot.record_feedback_message("/kaydet devam 5", self.session, 10, self.state)
        self.assertNotIn("checkpoints", self.state["tasks"][0])
        bot.set_action_message("/eylem Bir madde yaz", self.session)
        bot.record_feedback_message("/kaydet devam 5", self.session, 11, self.state)
        self.assertEqual(self.state["day"]["remaining_minutes"], 35)

    def test_manual_and_saved_actions_do_not_call_api(self):
        bot.build_plan_message("/plan", self.session, self.state)
        with patch.object(bot, "generate_next_action") as generate:
            bot.select_work_message("/sec 1 elle", self.session, self.state)
            self.session.pop("work")
            self.state["tasks"][0]["next_action"] = "Kayıtlı adım"
            bot.select_work_message("/sec 1", self.session, self.state)
            generate.assert_not_called()
        self.assertEqual(self.session["work"]["action"], "Kayıtlı adım")

    def test_repeated_delivery_and_restart_do_not_double_record(self):
        self.prepare(2)
        storage.save_state(self.state)
        update = {"update_id": 10, "message": {"from": {"id": 123},
                  "chat": {"id": 123, "type": "private"},
                  "date": int(datetime.now().timestamp()), "text": "/kaydet devam 5"}}
        bot.process_update(update, 123)
        with patch.object(bot, "telegram_request"):
            bot.flush_pending_reply("fake", 123)
        self.assertFalse(bot.process_update(update, 123))
        saved = storage.load_state()
        self.assertEqual(saved["day"]["remaining_minutes"], 35)
        self.assertEqual(len(saved["tasks"][1]["checkpoints"]), 1)
        self.assertEqual(saved["telegram"]["offset"], 11)

    def test_unauthorized_message_does_not_change_tasks_or_queue_reply(self):
        storage.save_state(self.state)
        bot.process_update({"update_id": 20, "message": {"from": {"id": 999},
                           "chat": {"id": 999, "type": "private"},
                           "date": int(datetime.now().timestamp()), "text": "/gun 0 1"}}, 123)
        saved = storage.load_state()
        self.assertEqual(saved["tasks"], self.state["tasks"])
        self.assertEqual(saved["day"], self.state["day"])
        self.assertIsNone(saved["telegram"]["pending_reply"])

    def test_old_day_feedback_is_rejected(self):
        self.prepare()
        before = copy.deepcopy(self.state)
        yesterday = datetime.now() - timedelta(days=1)
        bot.dispatch_message("/kaydet devam 5", self.session, self.state, 1, int(yesterday.timestamp()))
        self.assertEqual(self.state, before)

    def test_context_edit_invalidates_work_but_keeps_history(self):
        self.prepare()
        bot.record_feedback_message("/kaydet devam 5", self.session, 1, self.state)
        self.prepare()
        history = copy.deepcopy(self.state["tasks"][0]["checkpoints"])
        bot.edit_task_message("/duzenle task0001 | - | Yeni bağlam", self.session, self.state)
        self.assertFalse(self.session)
        self.assertIsNone(self.state["tasks"][0]["next_action"])
        self.assertEqual(self.state["tasks"][0]["checkpoints"], history)

    def test_completed_and_archived_tasks_can_be_reopened(self):
        for command in ("/tamamla", "/arsiv"):
            with self.subTest(command=command):
                bot.change_task_status_message(f"{command} task0001", self.session, 10, self.state)
                active, _ = planning.select_active_tasks(self.state["tasks"], 30, 3, date.today())
                self.assertNotIn("task0001", [item["id"] for item in active])
                bot.change_task_status_message("/ac task0001", self.session, 11, self.state)
                self.assertFalse(self.state["tasks"][0]["completed"])
                self.assertFalse(self.state["tasks"][0]["archived"])
                self.state["tasks"][0].pop("telegram_status_update_id")

    def test_pending_work_survives_restart_and_same_day_time_is_preserved(self):
        self.prepare()
        storage.save_state(self.state)
        saved = storage.load_state()
        self.assertEqual(saved["telegram"]["session"], self.session)
        with patch("builtins.input", return_value=""), redirect_stdout(io.StringIO()):
            self.assertEqual(cli.read_day(saved, date.today())["remaining_minutes"], 40)

    def test_transient_send_failure_keeps_reply_permanent_failure_unblocks(self):
        self.state["telegram"]["pending_reply"] = {"chat_id": 123, "text": "Yanıt"}
        storage.save_state(self.state)
        with patch.object(bot, "telegram_request", side_effect=RuntimeError("offline")):
            with self.assertRaises(RuntimeError):
                bot.flush_pending_reply("fake", 123)
        self.assertIsNotNone(storage.load_state()["telegram"]["pending_reply"])
        with patch.object(bot, "telegram_request", side_effect=ValueError("rejected")), redirect_stdout(io.StringIO()):
            bot.flush_pending_reply("fake", 123)
        saved = storage.load_state()
        self.assertIsNone(saved["telegram"]["pending_reply"])
        self.assertEqual(saved["tasks"], self.state["tasks"])

    def test_http_errors_are_classified_without_exposing_token(self):
        for code, exception in ((400, ValueError), (403, ValueError), (401, SystemExit),
                                (409, SystemExit), (429, RuntimeError), (500, RuntimeError)):
            with self.subTest(code=code), patch.object(bot, "urlopen", side_effect=HTTPError("secret-url", code, "error", {}, None)):
                with self.assertRaises(exception) as caught:
                    bot.telegram_request("fake-secret", "sendMessage", {})
                self.assertNotIn("fake-secret", str(caught.exception))
                self.assertNotIn("secret-url", str(caught.exception))

    def test_long_unicode_message_is_safe_and_original_is_not_mutated(self):
        original = "🧠" * 5000
        self.state["telegram"]["pending_reply"] = {"chat_id": 123, "text": original}
        storage.save_state(self.state)
        with patch.object(bot, "telegram_request", side_effect=RuntimeError("offline")) as send:
            with self.assertRaises(RuntimeError):
                bot.flush_pending_reply("fake", 123)
            sent = send.call_args.args[2]["text"]
            self.assertLessEqual(len(sent.encode("utf-16-le")) // 2, 4096)
        self.assertEqual(storage.load_state()["telegram"]["pending_reply"]["text"], original)


class CLITests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        patcher = patch.object(storage, "STATE_PATH", Path(directory.name) / "state.json")
        patcher.start()
        self.addCleanup(patcher.stop)
        self.state = {"tasks": [task(next_action="Bir madde yaz")],
                      "day": {"date": date.today().isoformat(), "remaining_minutes": 40, "energy": 3}}
        storage.save_state(self.state)

    def test_full_cli_manual_session_and_parent_completion(self):
        # Keep today's time, energy, plan, first task, unchanged context,
        # accept saved action, done, minutes, output, finish parent.
        answers = ["", "3", "q", "", "", "k", "done", "5", "Bir madde yazdım", "e"]
        with patch("builtins.input", side_effect=answers), patch.object(cli, "generate_next_action") as generate, redirect_stdout(io.StringIO()):
            cli.main()
            generate.assert_not_called()
        saved = storage.load_state()
        self.assertTrue(saved["tasks"][0]["completed"])
        self.assertIsNone(saved["tasks"][0]["next_action"])
        self.assertEqual(saved["day"]["remaining_minutes"], 35)
        self.assertEqual(saved["tasks"][0]["checkpoints"][0]["output_note"], "Bir madde yazdım")

    def test_cli_skip_does_not_create_checkpoint_or_spend_time(self):
        with patch("builtins.input", side_effect=["", "3", "q", "q"]), redirect_stdout(io.StringIO()):
            cli.main()
        saved = storage.load_state()
        self.assertNotIn("checkpoints", saved["tasks"][0])
        self.assertEqual(saved["day"]["remaining_minutes"], 40)

    def test_cli_context_edit_clears_the_same_action_as_telegram(self):
        with patch("builtins.input", side_effect=["d", "", "Yeni bağlam"]), redirect_stdout(io.StringIO()):
            cli.review_task_context(self.state["tasks"][0], self.state)
        saved = storage.load_state()
        self.assertEqual(saved["tasks"][0]["context"], "Yeni bağlam")
        self.assertIsNone(saved["tasks"][0]["next_action"])

    def test_filling_legacy_context_also_invalidates_old_action(self):
        item = self.state["tasks"][0]
        item.pop("desired_outcome")
        item.pop("context")
        with patch("builtins.input", side_effect=["Yeni hedef", "Yeni bağlam", ""]), redirect_stdout(io.StringIO()):
            cli.review_task_context(item, self.state)
        self.assertIsNone(storage.load_state()["tasks"][0]["next_action"])


class LLMAndReminderTests(unittest.TestCase):
    def test_llm_contract_and_invalid_responses(self):
        with patch.dict("os.environ", {"GROQ_API_KEY": "fake"}), patch.object(llm, "OpenAI") as client:
            response = client.return_value.responses.create.return_value
            response.status, response.output_text = "completed", "  Bir madde yaz.  "
            self.assertEqual(llm.generate_next_action("CV", 5, 2, [], "Hedef", "Bağlam"), "Bir madde yaz.")
            payload = json.loads(client.return_value.responses.create.call_args.kwargs["input"])
            self.assertEqual((payload["minutes"], payload["energy"], payload["context"]), (5, 2, "Bağlam"))
            for status, output in (("incomplete", "Adım"), ("completed", " "), ("completed", None)):
                response.status, response.output_text = status, output
                with self.subTest(status=status, output=output), self.assertRaises(ValueError):
                    llm.generate_next_action("CV", 5, 2, [])

    def test_missing_key_does_not_create_a_client(self):
        with patch.dict("os.environ", {}, clear=True), patch.object(llm, "OpenAI") as client:
            with self.assertRaises(ValueError):
                llm.generate_next_action("CV", 5, 2, [])
            client.assert_not_called()

    def test_reminder_is_once_daily_in_window_and_does_not_record_work(self):
        now = datetime.now().astimezone().replace(hour=9, minute=30, second=0)
        state = {"tasks": [task()], "day": None}
        runtime = storage.get_runtime(state, 123)
        reminders.configure_reminder("/hatirlat 09:00", state)
        self.assertTrue(reminders.queue_daily_reminder(state, 123, now))
        self.assertNotIn("checkpoints", state["tasks"][0])
        self.assertIsNone(state["day"])
        runtime["pending_reply"] = None
        self.assertFalse(reminders.queue_daily_reminder(state, 123, now))
        runtime["reminder"].pop("last_queued_date")
        self.assertFalse(reminders.queue_daily_reminder(state, 123, now.replace(hour=10, minute=0)))


if __name__ == "__main__":
    unittest.main()
