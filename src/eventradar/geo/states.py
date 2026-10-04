"""U.S. state and territory postal abbreviations and names."""

import re

STATE_NAMES = {
    "AL": "Alabama",
    "AK": "Alaska",
    "AZ": "Arizona",
    "AR": "Arkansas",
    "CA": "California",
    "CO": "Colorado",
    "CT": "Connecticut",
    "DE": "Delaware",
    "DC": "District of Columbia",
    "FL": "Florida",
    "GA": "Georgia",
    "HI": "Hawaii",
    "ID": "Idaho",
    "IL": "Illinois",
    "IN": "Indiana",
    "IA": "Iowa",
    "KS": "Kansas",
    "KY": "Kentucky",
    "LA": "Louisiana",
    "ME": "Maine",
    "MD": "Maryland",
    "MA": "Massachusetts",
    "MI": "Michigan",
    "MN": "Minnesota",
    "MS": "Mississippi",
    "MO": "Missouri",
    "MT": "Montana",
    "NE": "Nebraska",
    "NV": "Nevada",
    "NH": "New Hampshire",
    "NJ": "New Jersey",
    "NM": "New Mexico",
    "NY": "New York",
    "NC": "North Carolina",
    "ND": "North Dakota",
    "OH": "Ohio",
    "OK": "Oklahoma",
    "OR": "Oregon",
    "PA": "Pennsylvania",
    "RI": "Rhode Island",
    "SC": "South Carolina",
    "SD": "South Dakota",
    "TN": "Tennessee",
    "TX": "Texas",
    "UT": "Utah",
    "VT": "Vermont",
    "VA": "Virginia",
    "WA": "Washington",
    "WV": "West Virginia",
    "WI": "Wisconsin",
    "WY": "Wyoming",
    "PR": "Puerto Rico",
}
_BY_NAME = {name.lower(): abbr for abbr, name in STATE_NAMES.items()}


_COUNTRIES = frozenset({"us", "usa", "u.s.", "u.s.a.", "united states"})
_ZIP = re.compile(r"\s+\d{5}(?:-\d{4})?$")


def state_abbr(text: str) -> str | None:
    """
    Resolve a state written as an abbreviation or a full name.

    Parameters:
      text: `CA`, `ca`, or `California`.
    Returns:
      The postal abbreviation, or None when it names no state.
    """
    key = text.strip()
    if key.upper() in STATE_NAMES:
        return key.upper()
    return _BY_NAME.get(key.lower())


def with_state(address: str, state: str) -> str:
    """
    Add a state to an address that names none.

    The state goes before a trailing U.S. country name, else at the end:
    "Los Angeles, United States" becomes "Los Angeles, CA, United States".

    Parameters:
      address: One-line address.
      state: Postal abbreviation to add.
    Returns:
      The address, unchanged when any part already names a state.
    """
    parts = [p.strip() for p in address.split(",") if p.strip()]
    if any(state_abbr(_ZIP.sub("", p)) for p in parts):
        return address
    at = len(parts) - 1 if parts and parts[-1].lower() in _COUNTRIES else None
    parts.insert(len(parts) if at is None else at, state)
    return ", ".join(parts)
