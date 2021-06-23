"""State machine library for AppDaemon (Home Assistant).

Repository: https://github.com/PeWu/appdaemon-machine
"""

from collections import defaultdict, namedtuple
from copy import copy
from enum import Enum
from functools import partial
from urllib.parse import quote


# Pass ANY as the from_state to add a transition from all possible states.
ANY = object()


class Trigger:
  """Base class for all triggers."""

  def __init__(self):
    self.hass = None
    self.trigger_callback = None

  def initialize(self, hass, trigger_callback):
    """Called to initialize the trigger.
    Args:
      hass: The app inheriting appdaemon.plugins.hass.hassapi.Hass
      trigger_callback: Callback to run when the trigger is triggered.
    """
    self.hass = hass
    self.trigger_callback = trigger_callback

  # pylint: disable=no-self-use
  def activate(self):
    """Called to activate the trigger.

    Returns true if the trigger condition is already true. In this case the
    trigger does not call `trigger_callback`.
    """
    return False

  def suspend(self):
    """Called to suspend the trigger.

    When suspended, the trigger should not call `trigger_callback`.
    """

  def __str__(self):
    """Returns a string representation of the trigger."""
    return "Trigger"


class StateIs(Trigger):
  """A generic state-based trigger."""

  def __init__(self, entity, state_predicate):
    super().__init__()
    self.entity = entity
    self.state_predicate = state_predicate
    self.active = False

  def initialize(self, hass, trigger_callback):
    super().initialize(hass, trigger_callback)
    hass.listen_state(self._state_callback, self.entity)

  def activate(self):
    self.active = True
    entity_state = self.hass.get_state(self.entity)
    return self._test_predicate(entity_state)

  def suspend(self):
    self.active = False

  def _test_predicate(self, entity_state):
    try:
      return self.state_predicate(entity_state)
    except Exception:
      return False

  def _state_callback(
      self, unused_entity, unused_attribute, unused_old,
      new, unused_kwargs):
    if self.active and self._test_predicate(new):
      self.trigger_callback()


class StateEq(StateIs):
  """Trigger when entity state is equal to the given value."""
  def __init__(self, entity, value):
    super().__init__(entity, lambda v: v == value)
    self.value = value

  def __str__(self):
    return '{} == {}'.format(self.entity, self.value)


class StateNeq(StateIs):
  """Trigger when entity state is different than the given value."""
  def __init__(self, entity, value):
    super().__init__(entity, lambda v: v != value)
    self.value = value

  def __str__(self):
    return '{} != {}'.format(self.entity, self.value)


class StateOn(StateEq):
  """Trigger when entity state is on."""
  def __init__(self, entity):
    super().__init__(entity, 'on')

  def __str__(self):
    return self.entity


class StateOff(StateNeq):
  """Trigger when entity state is not on."""
  def __init__(self, entity):
    super().__init__(entity, 'on')

  def __str__(self):
    return '!{}'.format(self.entity)


class Timeout(Trigger):
  """Triggers after a certain time period."""

  def __init__(self, timeout_sec):
    super().__init__()
    self.timeout_sec = timeout_sec
    self.timer = None

  def activate(self):
    self.timer = self.hass.run_in(self._timer_callback, self.timeout_sec)

  def suspend(self):
    if self.hass.timer_running(self.timer):
      self.hass.cancel_timer(self.timer)
    self.timer = None

  def _timer_callback(self, unused_kwargs):
    self.trigger_callback()

  def __str__(self):
    return 'timeout {} s'.format(self.timeout_sec)


# Internal representation of a transition.
Transition = namedtuple('Transition', ['trigger', 'to_state', 'on_transition'])


class Machine:
  """State machine implementation for AppDaemon and Home Assistant."""

  def __init__(self, hass, states, initial=None, entity=None):
    """Initializes the state machine.

    If both `initial` and `entity` are provided:
    - if the entity in Home Assistant contains a valid state, it is used as the
      initial state
    - otherwise, `initial` is used as the initial state.

    Args:
      hass: The app inheriting appdaemon.plugins.hass.hassapi.Hass
      states: Enum with all possible states.
      initial: Initial state of the state machine. Defaults to the first state.
      entity: The entity that will mirror the state machine's state.
    """

    assert issubclass(states, Enum)
    assert isinstance(initial, states) or not initial

    self.hass = hass
    self.states = states
    self.state_entity = entity

    self.current_state = None

    if self.state_entity:
      # Try loading the state from Home Assistant.
      entity_state = self.hass.get_state(self.state_entity)
      if entity_state in {s.name for s in self.states}:
        self.current_state = self.states[entity_state]
      else:
        self.hass.log(
            'Unrecognized state: {}'.format(entity_state), level='WARNING')
      # Listen for state changes initiated in Home Assistant.
      self.hass.listen_state(self._state_callback, self.state_entity)

    if not self.current_state:
      self.current_state = initial or list(states)[0]

    if self.state_entity:
      self.hass.set_state(self.state_entity, state=self.current_state.name)

    self.on_transition_callback = None
    self.transitions = defaultdict(list)

  def _state_callback(
      self, unused_entity, unused_attribute, unused_old, new, unused_kwargs):
    """Called on change of the state entity."""

    # If the state name is not recognized, log a warning.
    if new not in {s.name for s in self.states}:
      self.hass.log('Unrecognized state: {}'.format(new), level='WARNING')
      return
    new_state = self.states[new]
    # No need to do a transition if the state doesn't change.
    if new_state != self.current_state:
      self._perform_transition(Transition(
          to_state=new_state, trigger=None, on_transition=None))

  def _perform_transition(self, transition):
    """Performs the given state transition."""

    from_state = self.current_state
    self.current_state = transition.to_state
    if self.state_entity:
      self.hass.set_state(self.state_entity, state=self.current_state.name)
    # self._start_timer()
    if transition.on_transition:
      transition.on_transition()
    if self.on_transition_callback:
      self.on_transition_callback(from_state, self.current_state)

    for state_transition in self.transitions[from_state]:
      state_transition.trigger.suspend()
    for state_transition in self.transitions[self.current_state]:
      triggered = state_transition.trigger.activate()
      # Immediately perform another transition if the trigger condition is
      # already met but only if the transition is to a different state.
      if triggered and self.current_state != state_transition.to_state:
        self._perform_transition(state_transition)
        break

  def _triggered(self, from_state, transition):
    """Called when a trigger has been triggered."""
    if self.current_state == from_state:
      self._perform_transition(transition)

  def add_transition(self, from_state, trigger, to_state, on_transition=None):
    """Adds a single transition.

    Args:
      from_state: The state from which the transition is made.
      trigger: The trigger causing this transition.
      to_state: Destination state of the transition.
      on_transition: Optional 0-argument callback to call when performing this
          transition.
    """

    assert from_state != ANY, 'Use add_transitions()'
    assert not isinstance(from_state, list), 'Use add_transitions()'
    assert isinstance(from_state, self.states), (
        'Invalid state: {}'.format(from_state))
    assert isinstance(to_state, self.states), (
        'Invalid state: {}'.format(to_state))
    assert not isinstance(trigger, list), 'Use add_transitions()'
    assert isinstance(trigger, Trigger), 'Invalid trigger'

    # Create a copy of the trigger object in case the same object is used to
    # create multiple transitions. One trigger object may only be used in one
    # transition.
    trigger = copy(trigger)

    transition = Transition(trigger, to_state, on_transition)
    self.transitions[from_state].append(transition)
    trigger.initialize(
        self.hass, partial(self._triggered, from_state, transition))
    if self.current_state == from_state:
      triggered = trigger.activate()
      # Immediately perform another transition if the trigger condition is
      # already met but only if the transition is to a different state.
      if triggered and self.current_state != to_state:
        self._perform_transition(transition)


  def add_transitions(
      self, from_states, triggers, to_state, on_transition=None):
    """Adds multiple transitions.

    Args:
      from_states: A single state or a list of states or ANY.
      trigger: A single trigger or multiple triggers.
      to_state: A single destination state of the transition.
      on_transition: Optional callback to call when performing this transition.
    """

    # If it's a single transition, call add_transition().
    if (from_states != ANY and not isinstance(from_states, list) and
        not isinstance(triggers, list)):
      self.add_transition(from_states, triggers, to_state, on_transition)
      return

    # Add transitions from all states.
    if from_states == ANY:
      for state in self.states:
        self.add_transitions(state, triggers, to_state, on_transition)
      return

    # Add transitions from a list of states.
    if isinstance(from_states, list):
      for state in from_states:
        self.add_transitions(state, triggers, to_state, on_transition)
      return

    # Add transitions for a list of triggers.
    if isinstance(triggers, list):
      for trigger in triggers:
        self.add_transitions(from_states, trigger, to_state, on_transition)
      return

  def on_transition(self, callback):
    """Sets a callback that will be called on each state transition.

    Only one callback can be set. Calls to on_transition() overwrite the
    previously set callback.

    Args:
      callback: function taking 2 arguments: (from_state, to_state)
    """
    self.on_transition_callback = callback

  def get_dot(self):
    """Returns the transition graph in DOT format."""

    edges = defaultdict(list)
    for from_state, transitions in self.transitions.items():
      for transition in transitions:
        edges[(from_state, transition.to_state)].append(transition.trigger)
    dot_edges = []
    for (from_state, to_state), triggers in edges.items():
      str_triggers = [str(trigger) for trigger in triggers]
      dot_edges.append('{}->{}[label="{}"];'.format(
          from_state.name, to_state.name, '\\n'.join(str_triggers)))

    return 'digraph G {{{}}}'.format(''.join(dot_edges))

  def log_graph_link(self):
    """Logs a link to a visualization of the transition graph."""

    link = 'https://dreampuf.github.io/GraphvizOnline/#{}'.format(
        quote(self.get_dot()))
    self.hass.log('Transition graph: {}'.format(link))
