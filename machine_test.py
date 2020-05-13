#!/usr/bin/python3

from collections import defaultdict, namedtuple
from enum import Enum
from unittest import main, TestCase
from unittest.mock import patch, Mock

from machine import Machine, ANY, StateEq, StateNeq, StateOn, StateOff, StateIs, Timeout


Timer = namedtuple('Timer', ['call_time', 'callback'])


class FakeHass:
  """Class imitating the methods from hassapi.Hass."""

  def __init__(self):
    self.callbacks = defaultdict(list)
    self.entities = {}
    self.current_time = 0
    self.timers = {}
    self.counter = 0

  def listen_state(self, callback, entity):
    self.callbacks[entity].append(callback)

  def set_state(self, entity, state):
    old_state = self.entities.get(entity)
    self.entities[entity] = state
    for callback in self.callbacks[entity]:
      callback(entity, None, old_state, state, None)

  def get_state(self, entity):
    return self.entities.get(entity)

  def run_in(self, callback, timeout_sec):
    self.counter += 1
    self.timers[self.counter] = Timer(
        self.current_time + timeout_sec, callback)
    return self.counter

  def log(self, message, level='INFO'):
    pass

  def cancel_timer(self, key):
    self.timers.pop(key, None)

  def advance_time(self, seconds):
    self.current_time += seconds
    callbacks_to_run = []
    for key, timer in self.timers.items():
      if self.current_time >= timer.call_time:
        callbacks_to_run.append(key)
    for key in callbacks_to_run:
      self.timers[key].callback(None)
      self.timers.pop(key, None)


class States(Enum):
  A = 1
  B = 2
  C = 3
globals().update(States.__members__)


class MachineTest(TestCase):

  def setUp(self):
    self.hass = FakeHass()
    self.hass.set_state('sensor.s', 'off')
    self.hass.set_state('sensor.t', 'value1')
    self.machine = Machine(self.hass, States)

  def test_first_state_is_initial(self):
    self.assertEqual(self.machine.current_state, A)

  def test_explicit_initial_state(self):
    machine = Machine(self.hass, States, initial=B)
    self.assertEqual(machine.current_state, B)

  def test_boolean_entity_triggers(self):
    self.machine.add_transition(A, StateOn('sensor.s'), B)
    self.machine.add_transition(B, StateOff('sensor.s'), A)
    self.assertEqual(self.machine.current_state, A)

    self.hass.set_state('sensor.s', 'on')
    self.assertEqual(self.machine.current_state, B)

    self.hass.set_state('sensor.s', 'off')
    self.assertEqual(self.machine.current_state, A)

  def test_valued_entity_triggers(self):
    self.machine.add_transition(A, StateEq('sensor.t', 'value2'), B)
    self.machine.add_transition(B, StateNeq('sensor.t', 'value2'), A)

    self.assertEqual(self.machine.current_state, A)

    self.hass.set_state('sensor.t', 'value2')
    self.assertEqual(self.machine.current_state, B)

    self.hass.set_state('sensor.t', 'value1')
    self.assertEqual(self.machine.current_state, A)

  def test_timeout_trigger(self):
    self.machine.add_transition(A, Timeout(10), B)
    self.machine.add_transition(B, Timeout(20), A)

    self.hass.advance_time(9)
    self.assertEqual(self.machine.current_state, A)
    self.hass.advance_time(1)
    self.assertEqual(self.machine.current_state, B)
    self.hass.advance_time(19)
    self.assertEqual(self.machine.current_state, B)
    self.hass.advance_time(1)
    self.assertEqual(self.machine.current_state, A)

  def test_transitions_cancels_timeout(self):
    self.machine.add_transition(A, StateOn('sensor.s'), B)
    self.machine.add_transitions(A, Timeout(10), C)

    self.hass.set_state('sensor.s', 'on')
    self.assertEqual(self.machine.current_state, B)

    self.hass.advance_time(10)
    self.assertEqual(self.machine.current_state, B)

  def test_transition_to_self_restarts_timer(self):
    self.machine.add_transition(A, StateOn('sensor.s'), A)
    self.machine.add_transitions(A, Timeout(10), B)

    self.hass.advance_time(5)
    self.hass.set_state('sensor.s', 'on')
    self.assertEqual(self.machine.current_state, A)
    self.hass.advance_time(5)
    self.assertEqual(self.machine.current_state, A)
    self.hass.advance_time(5)
    self.assertEqual(self.machine.current_state, B)

  def test_state_entity(self):
    machine = Machine(self.hass, States, entity='sensor.state')
    machine.add_transition(A, Timeout(10), B)

    self.assertEqual(self.hass.get_state('sensor.state'), 'A')
    self.hass.advance_time(10)
    self.assertEqual(self.hass.get_state('sensor.state'), 'B')

  def test_initial_state_from_hass(self):
    self.hass.set_state('sensor.state', 'B')
    machine = Machine(self.hass, States, entity='sensor.state')
    self.assertEqual(machine.current_state, B)

  def test_setting_state_from_hass(self):
    machine = Machine(self.hass, States, entity='sensor.state')
    self.assertEqual(machine.current_state, A)
    self.hass.set_state('sensor.state', 'B')
    self.assertEqual(machine.current_state, B)

  def test_from_any_state(self):
    trigger = Timeout(1)
    with patch.object(self.machine, 'add_transition') as add_transition:
      self.machine.add_transitions(ANY, trigger, A)

    add_transition.assert_any_call(A, trigger, A, None)
    add_transition.assert_any_call(B, trigger, A, None)
    add_transition.assert_any_call(C, trigger, A, None)
    self.assertEqual(add_transition.call_count, 3)

  def test_from_any_transition(self):
    self.machine.add_transitions(ANY, StateOn('sensor.s'), B)

    self.hass.set_state('sensor.s', 'on')
    self.assertEqual(self.machine.current_state, B)

  def test_from_state_list(self):
    trigger = Timeout(1)
    with patch.object(self.machine, 'add_transition') as add_transition:
      self.machine.add_transitions([A, B], trigger, C)

    add_transition.assert_any_call(A, trigger, C, None)
    add_transition.assert_any_call(B, trigger, C, None)
    self.assertEqual(add_transition.call_count, 2)

  def test_trigger_list(self):
    trigger1 = StateOn('sensor.s')
    trigger2 = StateOn('sensor.t')
    with patch.object(self.machine, 'add_transition') as add_transition:
      self.machine.add_transitions(A, [trigger1, trigger2], B)

    add_transition.assert_any_call(A, trigger1, B, None)
    add_transition.assert_any_call(A, trigger2, B, None)
    self.assertEqual(add_transition.call_count, 2)

  def test_state_and_trigger_list(self):
    trigger1 = StateOn('sensor.s')
    trigger2 = StateOn('sensor.t')
    with patch.object(self.machine, 'add_transition') as add_transition:
      self.machine.add_transitions([A, B], [trigger1, trigger2], C)

    add_transition.assert_any_call(A, trigger1, C, None)
    add_transition.assert_any_call(B, trigger1, C, None)
    add_transition.assert_any_call(A, trigger2, C, None)
    add_transition.assert_any_call(B, trigger2, C, None)
    self.assertEqual(add_transition.call_count, 4)

  def test_one_transition_callback(self):
    callback = Mock()
    self.machine.add_transition(A, StateOn('sensor.s'), B, callback)

    self.hass.set_state('sensor.s', 'on')
    callback.assert_called_once_with()

  def test_any_transition_callback(self):
    self.machine.add_transition(A, StateOn('sensor.s'), B)
    callback = Mock()
    self.machine.on_transition(callback)

    self.hass.set_state('sensor.s', 'on')
    callback.assert_called_once_with(A, B)

  def test_immediate_transition(self):
    """If a trigger condition is already met when entering a new state,
    immediately perform the transition."""
    self.machine.add_transition(A, StateOn('sensor.s'), B)
    self.machine.add_transition(B, StateEq('sensor.t', 'value1'), C)

    self.hass.set_state('sensor.s', 'on')
    self.assertEqual(self.machine.current_state, C)

  def test_initial_immediate_transition(self):
    """If a trigger condition is already met when entering a new state,
    immediately perform the transition."""
    self.machine.add_transition(A, StateOff('sensor.s'), B)
    self.assertEqual(self.machine.current_state, B)

  def test_lambda_state_trigger(self):
    self.machine.add_transition(
        A, StateIs('sensor.i', lambda v: int(v) > 5), B)
    self.assertEqual(self.machine.current_state, A)

    self.hass.set_state('sensor.i', '3')
    self.assertEqual(self.machine.current_state, A)

    self.hass.set_state('sensor.i', '6')
    self.assertEqual(self.machine.current_state, B)


if __name__ == '__main__':
  main()
