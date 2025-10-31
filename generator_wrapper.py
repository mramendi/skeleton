import inspect
import asyncio

class GeneratorWrapper:
    """
    Wraps a coroutine OR an async generator to provide a split
    interface for iterating yields and then getting the return value.

    Usage:

    wrapped = GeneratorWrapper(plugin_method())

    # Iterate over yields (if any)
    async for item in wrapped.yields():
        print(f"Got yield: {item}")

    # Get the final return value
    result = await wrapped.returns()
    print(f"Got return: {result}")

    NOTE: to return a value, a wrapped generator raises StopAsyncIteration
    For more detail, see backend/docs/raise-to-return-generator.md
    """

    def __init__(self, gen_or_coro):
        self.gen_or_coro = gen_or_coro
        # Check if it's an async generator
        self.is_generator = inspect.isasyncgen(self.gen_or_coro)

        # State variables
        self._return_value = None
        self._iteration_done = False
        self._iterator = None # Caches the generator's iterator
        self._coro_awaited = False # For coroutine path

    async def _run_coroutine(self):
        """Helper to run the coroutine once and store its result."""
        if not self._coro_awaited:
            try:
                self._return_value = await self.gen_or_coro
            finally:
                # Mark as done even if it fails, to prevent re-awaiting
                self._coro_awaited = True
                self._iteration_done = True
        return self._return_value

    async def yields(self):
        """
        An async generator that yields all items from the wrapped object.
        If the wrapped object is a coroutine, this yields nothing.
        """
        if self._iteration_done:
            # Safety check to prevent re-iteration
            return

        if not self.is_generator:
            # It's a coroutine. Run it now so .returns() has the value
            # the run happens here to make things the same as in the generator case -
            # all the heavy work is done in the async for i in wrapped.yields() loop
            await self._run_coroutine()
            return # This generator yields nothing.

        # It IS a generator. Iterate manually.
        self._iterator = self.gen_or_coro

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

        except Exception:
            self._iteration_done = True # Mark as done on any error
            raise # Re-raise other errors

    async def returns(self):
        """
        Awaits and returns the final `return` value.

        If .yields() has not been called or finished, this will
        run the generator/coroutine, discard all yields,
        and return the final value.
        """
        if self._iteration_done:
            # .yields() has already run and finished,
            # or it's a coroutine that has run.
            return self._return_value

        # .yields() has not been called.
        if not self.is_generator:
            # It's a coroutine, just run it.
            return await self._run_coroutine()

        # It's a generator, but .yields() was skipped.
        # Run it, discard yields, and get the return.
        async for _ in self.yields():
            pass # Discard all yielded values

        # Now self._iteration_done is True and self._return_value is set.
        return self.return_value
