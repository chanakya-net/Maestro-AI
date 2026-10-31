# Reading summaries

`summarize_readings` clamps finite readings to inclusive bounds, then returns
the statistics for those normalized values together with the normalized list.

```python
from examples.runtime_smoke.summary.summarize import summarize_readings

summary = summarize_readings([-5, 20, 150])
# summary["values"] == [0, 20, 100]
# summary["count"] == 3
```

Pass custom bounds when needed:

```python
summary = summarize_readings([-10, 0, 10], minimum=-5, maximum=5)
# summary["values"] == [-5, 0, 5]
```
