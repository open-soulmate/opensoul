"""Smart Calculator Plugin Backend — 数学表达式求解、单位转换。"""

import math
import time
import re
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter()

# ── State ────────────────────────────────────────────────────
history: list[dict] = []
MAX_HISTORY = 100

# ── Safe math evaluation ────────────────────────────────────
SAFE_NAMES = {
    "abs": abs, "round": round, "min": min, "max": max,
    "sum": sum, "pow": pow, "int": int, "float": float,
    "pi": math.pi, "e": math.e, "tau": math.tau,
    "sin": math.sin, "cos": math.cos, "tan": math.tan,
    "asin": math.asin, "acos": math.acos, "atan": math.atan,
    "sqrt": math.sqrt, "log": math.log, "log2": math.log2, "log10": math.log10,
    "ceil": math.ceil, "floor": math.floor, "factorial": math.factorial,
    "degrees": math.degrees, "radians": math.radians,
    "hypot": math.hypot, "gcd": math.gcd,
}


def safe_eval(expr: str) -> float:
    """Evaluate a math expression safely (no builtins, no imports)."""
    # Strip dangerous patterns
    for forbidden in ["import", "exec", "eval", "open", "__", "os.", "sys.", "subprocess"]:
        if forbidden in expr:
            raise ValueError(f"Forbidden expression: contains '{forbidden}'")
    result = eval(expr, {"__builtins__": {}}, SAFE_NAMES)
    return float(result)


# ── Unit conversion tables ───────────────────────────────────
LENGTH_TO_M = {
    "mm": 0.001, "cm": 0.01, "m": 1.0, "km": 1000.0,
    "in": 0.0254, "ft": 0.3048, "yd": 0.9144, "mi": 1609.344,
    "nm": 1.852e3, "um": 1e-6, "ly": 9.461e15,
}
WEIGHT_TO_KG = {
    "mg": 1e-6, "g": 0.001, "kg": 1.0, "t": 1000.0,
    "oz": 0.0283495, "lb": 0.453592, "st": 6.35029,
}
TEMP_CONVERSIONS = {
    ("c", "f"): lambda v: v * 9 / 5 + 32,
    ("c", "k"): lambda v: v + 273.15,
    ("f", "c"): lambda v: (v - 32) * 5 / 9,
    ("f", "k"): lambda v: (v - 32) * 5 / 9 + 273.15,
    ("k", "c"): lambda v: v - 273.15,
    ("k", "f"): lambda v: (v - 273.15) * 9 / 5 + 32,
}
SPEED_TO_MS = {
    "m/s": 1.0, "km/h": 1 / 3.6, "mph": 0.44704, "knot": 0.514444, "mach": 343.0,
}
AREA_TO_M2 = {
    "mm2": 1e-6, "cm2": 1e-4, "m2": 1.0, "km2": 1e6,
    "ha": 1e4, "acre": 4046.86, "ft2": 0.092903, "in2": 6.4516e-4,
}
VOLUME_TO_L = {
    "ml": 0.001, "l": 1.0, "m3": 1000.0, "gal": 3.78541,
    "qt": 0.946353, "pt": 0.473176, "cup": 0.236588,
    "fl_oz": 0.0295735, "tbsp": 0.0147868, "tsp": 0.00492892,
}

UNIT_TABLES = {
    "length": LENGTH_TO_M,
    "weight": WEIGHT_TO_KG,
    "speed": SPEED_TO_MS,
    "area": AREA_TO_M2,
    "volume": VOLUME_TO_L,
}


def convert_units(value: float, from_unit: str, to_unit: str) -> float:
    """Convert between units."""
    from_unit = from_unit.lower().strip()
    to_unit = to_unit.lower().strip()

    # Temperature
    if from_unit in ("c", "f", "k") and to_unit in ("c", "f", "k"):
        fn = TEMP_CONVERSIONS.get((from_unit, to_unit))
        if fn:
            return fn(value)
        raise ValueError(f"Cannot convert {from_unit} → {to_unit}")

    # Find matching table
    for table_name, table in UNIT_TABLES.items():
        if from_unit in table and to_unit in table:
            base = value * table[from_unit]
            return base / table[to_unit]

    raise ValueError(f"Unknown unit conversion: {from_unit} → {to_unit}")


# ── Request Models ───────────────────────────────────────────

class CalculateRequest(BaseModel):
    expression: str


class ConvertRequest(BaseModel):
    value: float
    from_unit: str
    to_unit: str


# ── Endpoints ────────────────────────────────────────────────

@router.post("/calculate")
async def calculate(req: CalculateRequest):
    """Evaluate a mathematical expression."""
    start = time.time()
    try:
        result = safe_eval(req.expression)
    except ZeroDivisionError:
        raise HTTPException(400, "Division by zero")
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(400, f"Invalid expression: {e}")

    elapsed_ms = (time.time() - start) * 1000

    entry = {
        "type": "calculate",
        "input": req.expression,
        "output": str(result),
        "timestamp": time.time(),
    }
    history.insert(0, entry)
    if len(history) > MAX_HISTORY:
        history.pop()

    return {
        "expression": req.expression,
        "result": result,
        "result_int": int(result) if result == int(result) else None,
        "elapsed_ms": round(elapsed_ms, 2),
    }


@router.post("/convert")
async def convert(req: ConvertRequest):
    """Convert between units."""
    try:
        result = convert_units(req.value, req.from_unit, req.to_unit)
    except ValueError as e:
        raise HTTPException(400, str(e))

    entry = {
        "type": "convert",
        "input": f"{req.value} {req.from_unit} → {req.to_unit}",
        "output": str(result),
        "timestamp": time.time(),
    }
    history.insert(0, entry)
    if len(history) > MAX_HISTORY:
        history.pop()

    return {
        "value": req.value,
        "from_unit": req.from_unit,
        "to_unit": req.to_unit,
        "result": result,
    }


@router.get("/history")
async def get_history(limit: int = 20):
    """Get calculation history."""
    return {"history": history[:limit], "total": len(history)}


@router.delete("/history")
async def clear_history():
    """Clear calculation history."""
    history.clear()
    return {"message": "History cleared"}


@router.get("/units")
async def list_units():
    """List all supported units."""
    return {
        "categories": {
            "length": list(LENGTH_TO_M.keys()),
            "weight": list(WEIGHT_TO_KG.keys()),
            "temperature": ["c", "f", "k"],
            "speed": list(SPEED_TO_MS.keys()),
            "area": list(AREA_TO_M2.keys()),
            "volume": list(VOLUME_TO_L.keys()),
        }
    }


@router.get("/health")
async def plugin_health():
    """Smart Calculator plugin health."""
    return {
        "status": "ok",
        "component": "SmartCalc",
        "history_count": len(history),
        "supported_functions": len(SAFE_NAMES),
        "unit_categories": len(UNIT_TABLES) + 1,  # +1 for temperature
    }
