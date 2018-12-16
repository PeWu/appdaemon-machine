# State machine library for AppDaemon (Home Assistant)

Define and run state machines to drive your automations using [AppDaemon](https://appdaemon.readthedocs.io/) with [Home Assistant]().

[![Build Status](https://travis-ci.com/PeWu/appdaemon-machine.svg?branch=master)](https://travis-ci.com/PeWu/appdaemon-machine)

## Installation

1. Clone the appdaemon-machine repository.
```
git clone https://github.com/PeWu/appdaemon-machine.git
```
2. Copy the `machine.py` file to your apps directory.
```
cp appdaemon-machine/machine.py ~/.homeassistant/apps
```

## Example

Simple example:
```python
import appdaemon.plugins.hass.hassapi as hass
from enum import Enum
from machine import Machine, ANY, StateEq, StateNeq, Timeout

class States(Enum):
  HOME = 1
  AWAY = 2
  LEAVING = 3
globals().update(States.__members__) # Make the states accessible without the States. prefix.

class Presence(hass.Hass):
  def initialize(self):
    machine = Machine(self, States)

    machine.add_transitions(ANY, StateEq('device_tracker.my_phone', 'home'), HOME)
    machine.add_transition(HOME, StateNeq('device_tracker.my_phone', 'home'), LEAVING)
    machine.add_transition(LEAVING, Timeout(30), AWAY, on_transition=self.on_away)

    machine.log_graph_link()

  def on_away(self):
    # e.g. turn off the lights.
```

The `log_graph_link()` call will log [this link](https://dreampuf.github.io/GraphvizOnline/#digraph%20G%20%7BLEAVING-%3EAWAY%5Blabel%3D%22timeout%2030%20s%22%5D%3BAWAY-%3EHOME%5Blabel%3D%22my_phone%20%3D%3D%20home%22%5D%3BLEAVING-%3EHOME%5Blabel%3D%22my_phone%20%3D%3D%20home%22%5D%3BHOME-%3ELEAVING%5Blabel%3D%22my_phone%20!%3D%20home%22%5D%3BHOME-%3EHOME%5Blabel%3D%22my_phone%20%3D%3D%20home%22%5D%3B%7D) on the console.


## API

### class **Machine**(hass, states, initial=None, entity=None)
Initializes the state machine.

If both `initial` and `entity` are provided:
- if the entity in Home Assistant contains a valid state, it is used as the
  initial state
- otherwise, `initial` is used as the initial state.

#### Args
`hass`: The app inheriting appdaemon.plugins.hass.hassapi.Hass

`states`: Enum with all possible states.

`initial`: Initial state of the state machine. Defaults to the first state.

`entity`: The entity that will mirror the state machine's state.

### Machine.add_transition(from_state, trigger, to_state, on_transition=None):
Adds a single transition.

#### Args
`from_state`: The state from which the transition is made.

`trigger`: The trigger causing this transition.

`to_state`: Destination state of the transition.

`on_transition`: Optional 0-argument callback to call when performing this transition.

### Machine.add_transitions(from_states, triggers, to_state, on_transition=None)
Adds multiple transitions.

Examples:
```python
# 2 transitions, one from STATE1, one from STATE2.
machine.add_transitions([STATE1, STATE2], StateOn('binary_sensor.sensor1'), STATE3)
# 2 transitions for 2 different triggers.
machine.add_transitions(STATE1, [StateOn('binary_sensor.sensor1'), Timeout(5)], STATE3)
# 4 transitions.
machine.add_transitions(
    [STATE1, STATE2],
    [StateOn('binary_sensor.sensor1'), Timeout(5)],
    STATE3)
# One transition from each state to STATE3 (including STATE3->STATE3)
machine.add_transitions(ANY, StateOn('binary_sensor.sensor1'), STATE3)
```
#### Args
`from_states`: A single state or a list of states or ANY.

`trigger`: A single trigger or multiple triggers.

`to_state`: A single destination state of the transition.

`on_transition`: Optional callback to call when performing this transition.

### Machine.on_transition(callback)
Sets a callback that will be called on each state transition.

Only one callback can be set. Calls to `on_transition()` overwrite the previously set callback.

#### Args
`callback`: function taking 2 arguments: (`from_state`, `to_state`)

### Machine.get_dot()
Returns the transition graph in [DOT](https://en.wikipedia.org/wiki/DOT_(graph_description_language)) format.

### Machine.log_graph_link()
Logs a link to a visualization of the transition graph.

### Triggers
Example triggers:
```python
StateOn('binary_sensor.sensor1')
StateOff('binary_sensor.sensor2')
StateEq('device_tracker.device1', 'home')
StateNeq('device_tracker.device2', 'home')
Timeout(10) # seconds
```

## Not supported yet
* Setting the state explicitly from code, e.g. `machine.set_state(HOME)`.
* Arbitrary predicates, e.g. `temperature < 20`. You can create a template sensor in Home Assistant as a workaround.
