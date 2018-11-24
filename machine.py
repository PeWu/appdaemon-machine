"""State machine library for AppDaemon (Home Assistant).

Repository: https://github.com/PeWu/appdaemon-machine
"""

from collections import defaultdict, namedtuple
from enum import Enum
from urllib.parse import quote


# Pass ANY as the from_state to add a transition from all possible states.
class ANY:
  pass


# Transition triggers.
IsState = namedtuple('IsState', ['entity', 'value'])
IsState.__new__.__defaults__ = ('on',) # Default value = 'on'

IsNotState = namedtuple('IsNotState', ['entity', 'value'])
IsNotState.__new__.__defaults__ = ('on',) # Default value = 'on'

Timeout = namedtuple('Timeout', ['timeout_sec'])


# Internal representation of a transition.
Transition = namedtuple('Transition', ['trigger', 'to_state', 'on_transition'])


def trigger_to_string(trigger):
  """Returns a human-readable description of a trigger."""

  if isinstance(trigger, IsState):
    entity = trigger.entity.split('.')[1]
    if trigger.value == 'on':
      return entity
    return '{} == {}'.format(entity, trigger.value)
  if isinstance(trigger, IsNotState):
    entity = trigger.entity.split('.')[1]
    if trigger.value == 'on':
      return '!{}'.format(entity)
    return '{} != {}'.format(entity, trigger.value)
  if isinstance(trigger, Timeout):
    return 'timeout {} s'.format(trigger.timeout_sec)


class Machine:
  """State machine implementation for AppDaemon and Home Assistant."""

  def __init__(self, hass, states, initial = None, entity = None):
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
            'Unrecognized state: {}'.format(entity_state), level = 'WARNING')
      # Listen for state changes initiated in Home Assistant.
      self.hass.listen_state(self._state_callback, self.state_entity)

    if not self.current_state:
      self.current_state = initial or list(states)[0]

    if self.state_entity:
      self.hass.set_state(self.state_entity, state = self.current_state.name)

    self.timer = None
    self.timeout_transitions = {}
    self.state_transitions = defaultdict(list)
    self.watched_entities = set()
    self.on_transition_callback = None

  def _entity_callback(self, entity, attribute, old, new, kwargs):
    """Called on change of a watched entity."""

    for transition in self.state_transitions[self.current_state]:
      if transition.trigger.entity == entity:
        if ((isinstance(transition.trigger, IsState) and
            new == transition.trigger.value) or
            (isinstance(transition.trigger, IsNotState) and
            new != transition.trigger.value)):
          self._perform_transition(transition)
          return

  def _state_callback(self, entity, attribute, old, new, kwargs):
    """Called on change of the state entity."""

    # If the state name is not recognized, log a warning.
    if new not in {s.name for s in self.states}:
      self.hass.log('Unrecognized state: {}'.format(new), level = 'WARNING')
      return
    new_state = self.states[new]
    # No need to do a transition if the state doesn't change.
    if new_state != self.current_state:
      self._perform_transition(Transition(
          to_state = new_state, trigger = None, on_transition = None))

  def _timer_callback(self, kwargs):
    """Called by timer."""

    self.timer = None
    transition = self.timeout_transitions[self.current_state]
    self._perform_transition(transition)

  def _perform_transition(self, transition):
    """Performs the given state transition."""

    from_state = self.current_state
    self.current_state = transition.to_state
    if self.state_entity:
      self.hass.set_state(self.state_entity, state = self.current_state.name)
    self._start_timer()
    if transition.on_transition:
      transition.on_transition()
    if self.on_transition_callback:
      self.on_transition_callback(from_state, self.current_state)

  def _start_timer(self):
    """Starts a new timer cancelling an existing one if necessary."""

    if self.timer:
      self.hass.cancel_timer(self.timer)
    if self.current_state in self.timeout_transitions:
      timeout_sec = (
          self.timeout_transitions[self.current_state].trigger.timeout_sec)
      self.timer = self.hass.run_in(self._timer_callback, timeout_sec)

  def add_transition(self, from_state, trigger, to_state, on_transition = None):
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

    # Add transition based on a state trigger.
    if isinstance(trigger, IsState) or isinstance(trigger, IsNotState):
      self.state_transitions[from_state].append(
          Transition(trigger, to_state, on_transition))
      entity = trigger.entity
      if entity not in self.watched_entities:
        self.watched_entities.add(entity)
        self.hass.listen_state(self._entity_callback, entity)

    # Add transition based on a timeout trigger.
    elif isinstance(trigger, Timeout):
      self.timeout_transitions[from_state] = Transition(
          trigger, to_state, on_transition)
      if from_state == self.current_state:
        self._start_timer()

    else:
      raise RuntimeError("Triggers must be IsState/IsNotState/Timeout")

  def add_transitions(
      self, from_states, triggers, to_state, on_transition = None):
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
    for from_state, transitions in self.state_transitions.items():
      for transition in transitions:
        edges[(from_state, transition.to_state)].append(transition.trigger)
    for from_state, transition in self.timeout_transitions.items():
      edges[(from_state, transition.to_state)].append(transition.trigger)
    dot_edges = []
    for (from_state, to_state), triggers in edges.items():
      dot_triggers = [trigger_to_string(trigger) for trigger in triggers]
      dot_edges.append('{}->{}[label="{}"];'.format(
          from_state.name, to_state.name, '\\n'.join(dot_triggers)))

    return 'digraph G {{{}}}'.format(''.join(dot_edges))

  def log_graph_link(self):
    """Logs a link to a visualization of the transition graph."""

    link = 'https://dreampuf.github.io/GraphvizOnline/#{}'.format(
        quote(self.get_dot()))
    self.hass.log('Transition graph: {}'.format(link))
