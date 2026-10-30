"""Bounded native quality trials; no punctuation, voice, speed or pause edits."""
from contextlib import contextmanager


@contextmanager
def native_step_override(engine,total_steps):
    if type(total_steps) is not int or total_steps not in (8,10,12):
        raise ValueError('Unsupported bounded quality setting')
    if total_steps==8:
        yield
        return
    original=engine.synthesize
    sentinel=object();owned=vars(engine).get('synthesize',sentinel)
    def controlled(*args,**kwargs):
        if kwargs.get('total_steps')!=8:
            raise ValueError('Unexpected baseline native steps')
        return original(*args,**(kwargs|{'total_steps':total_steps}))
    engine.synthesize=controlled
    try:
        yield
    finally:
        if owned is sentinel:del engine.synthesize
        else:engine.synthesize=owned
