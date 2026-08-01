#!/usr/bin/env python3
"""
Reference implementation of the specimen label check-digit scheme described
in specimen_label_allocation.csv: "Modulus 11, weights 2 to 7 applied right
to left, remainder 10 recorded as X."

This mirrors, digit for digit, the calculate chain implemented in the
XLSForm (cd_d1..cd_d6, cd_weighted_sum, cd_remainder, cd_expected) so that
the same logic can be unit-tested outside of JavaRosa/ODK Collect, where it
cannot be directly unit tested.

Run: python3 check_digit.py
"""
from itertools import combinations


def check_digit(label6: str) -> str:
    """label6: 6-digit string, e.g. '480123'. Returns '0'-'9' or 'X'."""
    if len(label6) != 6 or not label6.isdigit():
        raise ValueError(f"label must be 6 digits, got {label6!r}")
    digits = [int(c) for c in label6]  # d1..d6, left to right
    # weights 2..7 applied right to left: d6*2 + d5*3 + d4*4 + d3*5 + d2*6 + d1*7
    weights_right_to_left = [7, 6, 5, 4, 3, 2]  # aligned to d1..d6
    weighted_sum = sum(d * w for d, w in zip(digits, weights_right_to_left))
    remainder = weighted_sum % 11
    return "X" if remainder == 10 else str(remainder)


def full_label(label6: str) -> str:
    return f"BSN{label6}-{check_digit(label6)}"


def is_valid(label6: str, cd: str) -> bool:
    return check_digit(label6) == cd.upper()


def transpositions_all_detected(label6: str) -> bool:
    """Confirms every pairwise digit transposition changes the check digit
    (proof that this weight scheme - consecutive integers 2..7, all coprime
    to 11 - catches transpositions at ANY two positions, not only adjacent
    ones, as noted in the constraint register, item C-09)."""
    original_cd = check_digit(label6)
    digits = list(label6)
    ok = True
    for i, j in combinations(range(6), 2):
        if digits[i] == digits[j]:
            continue  # transposing equal digits is not a detectable error
        swapped = digits.copy()
        swapped[i], swapped[j] = swapped[j], swapped[i]
        swapped_label = "".join(swapped)
        if check_digit(swapped_label) == original_cd:
            print(f"  UNDETECTED transposition at positions {i},{j}: "
                  f"{label6} -> {swapped_label}")
            ok = False
    return ok


if __name__ == "__main__":
    print("=== Worked example ===")
    example = "480123"
    print(f"Label {example} -> check digit {check_digit(example)} "
          f"-> full label {full_label(example)}")

    print("\n=== Test plan cases (see test_plan.csv items TC-11 to TC-15) ===")
    cases = [
        # (label6, entered_check_digit, expect_valid, description)
        ("480123", check_digit("480123"), True, "Valid label, correct check digit"),
        ("480123", "9" if check_digit("480123") != "9" else "8",
         False, "Valid label, WRONG check digit (single altered check digit)"),
        ("480213", check_digit("480123"), False,
         "Adjacent transposition (digits at positions 4-5 swapped: "
         "1,2 -> 2,1) with the ORIGINAL check digit - must be rejected"),
        ("180423", check_digit("480123"), False,
         "Non-adjacent transposition (digits at positions 1 and 4 swapped: "
         "4,1 -> 1,4) with the ORIGINAL check digit - must be rejected"),
        ("000000", check_digit("000000"), True,
         "Edge case: all-zero digits, remainder 0"),
    ]
    for label6, cd, expect_valid, desc in cases:
        actual_valid = is_valid(label6, cd)
        status = "PASS" if actual_valid == expect_valid else "FAIL"
        print(f"[{status}] {desc}: label={label6} cd_entered={cd} "
              f"cd_expected={check_digit(label6)} valid={actual_valid} "
              f"(expected {expect_valid})")

    print("\n=== Exhaustive transposition-detection proof, 20 sample labels ===")
    import random
    random.seed(7)
    all_ok = True
    for _ in range(20):
        lbl = f"{random.randint(480000, 501599):06d}"
        if not transpositions_all_detected(lbl):
            all_ok = False
    print("All pairwise transpositions detected on all sample labels:", all_ok)

    print("\n=== Remainder-10 (X) case search ===")
    for n in range(480000, 480050):
        lbl = f"{n:06d}"
        if check_digit(lbl) == "X":
            print(f"  {lbl} -> check digit X (full label {full_label(lbl)})")
            break
