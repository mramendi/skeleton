# The Raise-to-Return (R2R) Generator Pattern

## 1. Executive Summary

The "Raise-to-Return" (R2R) pattern is the **standard way** in this project to create an `async` function that can both `yield` streaming progress updates and provide a final, structured result. It is used for function calls and tool calls (both class-based and function-based tools).

* **The Problem:** Python (all versions) raises a `SyntaxError` if you use `return <value>` inside an `async def` function that also has a `yield` statement.
* **The Solution:** Instead of `return result`, you must use `raise StopAsyncIteration(result)`.
* **The Consumer:** Our `GeneratorWrapper` utility is specifically designed to `try...except` this `StopAsyncIteration` and extract the `e.value` (your result) from it.

The general outline of this solution, "achieve teh same result nby raising an exception", is blessed here https://discuss.python.org/t/allow-return-statements-with-values-in-asynchronous-generators/66886/47

This document outlines the pattern and provides clear examples for tool writers.

---

## 2. How The Pattern Works

### The `SyntaxError`

You cannot do this. The Python parser will fail before your code ever runs.

```python
# THIS IS WRONG. IT WILL NOT RUN.
async def my_broken_tool():
    yield "Progress update..."

    # This line causes:
    # SyntaxError: 'return' with value in async generator
    return {"final": "data"}
```

### The R2R Solution

You must use `raise StopAsyncIteration(value)` to "return" your final value. This is the explicit, syntactically-legal mechanism that `return` is just sugar for in *synchronous* generators.

```python
# THIS IS CORRECT.
async def my_r2r_tool():
    yield "Progress update..."

    final_result = {"final": "data"}

    # This correctly "returns" the value
    raise StopAsyncIteration(final_result)
```

### The "Consumer" Side (`GeneratorWrapper`)

Our `GeneratorWrapper` (in `generator_wrapper.py`) is the "consumer" that makes this pattern work. It wraps your R2R generator and contains this key logic:

```python
# Inside GeneratorWrapper.yields()...
try:
    while True:
        # Manually get the next item
        item = await anext(self._iterator)
        yield item # Pass the yield up
except StopAsyncIteration as e:
    self._return_value = e.args[0] if e.args else None # Store return value
    self._iteration_done = True
except RuntimeError as e:
    # This handles the PEP 479 case where the runtime
    # converts StopAsyncIteration into a RuntimeError.

    # Check if this RuntimeError was *caused* by
    # our StopAsyncIteration.
    if isinstance(e.__cause__, StopAsyncIteration):
        # This is our R2R signal!
        stop_exc = e.__cause__
        self._return_value = stop_exc.args[0] if stop_exc.args else None
        self._iteration_done = True
    else:
        # This is a *real* RuntimeError. Re-raise it.
        self._iteration_done = True
        raise e
```

This allows our `DefaultMessageProcessor` to cleanly get *both* the yields and the final result. It gets around the conversion of the `StopAsyncInteration` into a `RuntimeError` by Python.

---

## 3. The Contract: How to Write Tools and functions

All tools and functions must follow this contract. This includes function-based tools wrapped by `FunctionToolWrapper`. (We use function-based tools in the examples, but the pattern applies to other cases)

### Option 1: Simple Tools (Coroutines)

If your tool does **not** need to stream progress, just write a normal `async def` function and use `return`.

* **Rule:** Use `async def` and `return`.
* **Do NOT** use `yield`.

**Example: `ping.py`**
```python
import asyncio

async def ping(query: str) -> str:
    """
    Returns a simple "pong" string.
    """
    await asyncio.sleep(0.1) # Simulate I/O

    # Just return the final value.
    return f"pong to your {query}"
```

### Option 2: Streaming Tools (R2R Generators)

If your tool **does** need to stream progress, you must use the R2R pattern.

* **Rule 1:** Use `async def` and `yield` for progress.
* **Rule 2:** Use `raise StopAsyncIteration(final_value)` to return the final result.
* **Rule 3:** Do **NOT** use `return <value>`.

**Example: `streaming_weather.py`**
```python
import asyncio
from typing import Dict, Any

async def get_weather(location: str, unit: str = "celsius") -> Dict[str, Any]:
    """
    Gets the weather, yielding progress updates.

    NOTE: The '-> Dict' return hint is for the 'llmio' schema builder.
    We must use 'type: ignore[async-generator-return]'
    to satisfy mypy, which knows this is a generator.
    """

    # 1. Yield progress as many times as you want
    yield f"Contacting weather service for {location}..."
    await asyncio.sleep(0.5)

    yield "Receiving data..."
    await asyncio.sleep(0.5)

    # 2. Prepare your final result
    final_result = {
        "location": location,
        "temperature": 22,
        "unit": unit,
        "forecast": "sunny"
    }

    # 3. Use 'raise StopAsyncIteration' to "return" the result
    raise StopAsyncIteration(final_result)
```

---

## 4. `FunctionToolWrapper` (For Reference)

Our internal `FunctionToolWrapper` (the "double-wrap") **also** follows this pattern. It is a "delegating generator." It wraps your tool, `yield`s all its progress, and when your tool finishes, it catches *your* `StopAsyncIteration`, gets the return value, and then `raise`s its *own* `StopAsyncIteration` to pass the value up to the `DefaultMessageProcessor`.

This ensures the R2R pattern is the single, consistent contract for all function and tool plugins in the system.
