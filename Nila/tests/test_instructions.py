

def test_the_offline_engine_can_use_a_standing_rule(brain, vault):
    """The gap the acceptance run found: with no model configured, "call me
    Anna" reached every engine except the one an offline PC uses."""
    brain.memory.add_instruction("call me Anna")
    turn = brain.handle("what do you call me")
    assert "Anna" in turn.answer


def test_an_online_turn_still_gets_no_instruction(config, vault, memory):
    """The offline fix must not leak a rule onto the wire."""
    from brain.agent.agent import Brain
    from brain.agent.providers import Completion, LLMProvider

    class FakeOnline(LLMProvider):
        name, needs_network = "fake-online", True

        def available(self):
            return True

        def complete(self, system, messages, tools=None, tools_mode="auto"):
            self.seen = system
            return Completion(text="ok", model="fake-online", provider=self.name)

    fake = FakeOnline()
    brain = Brain(config, vault, memory, provider=fake)
    brain.memory.add_instruction("call me Anna")
    assert "Anna" not in brain.system_prompt("what do you call me")


def test_the_router_and_the_brain_share_one_offline_engine(brain):
    """Two LocalBrain objects was the bug: rules were loaded onto one and read
    from the other, so the offline answer ignored them."""
    assert brain.router is not None
    assert brain.router.builtin is brain.local


def test_a_rule_survives_the_ollama_not_running_path(brain, vault):
    brain.memory.add_instruction("call me Anna")
    turn = brain.handle("hello")
    assert turn.provider in ("local", "local(fallback)")
    assert "Anna" in turn.answer
