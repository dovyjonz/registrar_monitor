from registrarmonitor.website.availability import calculate_availability


def section(section_type, enrollment, capacity):
    return {
        "type": section_type,
        "currentEnrollment": enrollment,
        "currentCapacity": capacity,
    }


def test_one_type_uses_seats_and_preserves_actual_totals():
    result = calculate_availability(
        {
            "1": section("Lecture", 26, 30),
            "2": section("Lecture", 32, 30),
        }
    )

    assert result["available"] == 4
    assert result["sentence"] == "4 seats open — 58/60 enrolled."


def test_multi_type_uses_minimum_available_places_and_reports_breakdown():
    result = calculate_availability(
        {
            "L1": section("Lecture", 28, 30),
            "B1": section("Lab", 19, 20),
            "B2": section("Lab", 20, 20),
            "B3": section("Lab", 20, 20),
            "B4": section("Lab", 20, 20),
            "T1": section("Tutorial", 17, 20),
        }
    )

    assert result["available"] == 1
    assert result["limitingTypes"] == ["Lab"]
    assert result["sentence"] == "1 registration place available — Limited by lab."
    assert "Labs 1/4 open" in result["breakdown"]


def test_tied_limiting_types_are_all_reported():
    result = calculate_availability(
        {
            "L": section("Lecture", 29, 30),
            "R": section("Tutorial", 19, 20),
        }
    )

    assert result["limitingTypes"] == ["Lecture", "Tutorial"]
    assert result["available"] == 1


def test_registrar_section_codes_use_the_dashboard_display_names():
    result = calculate_availability(
        {
            "1L": section("L", 25, 30),
            "1Lb": section("B", 15, 20),
        }
    )

    assert result["limitingTypes"] == ["Lab", "Lecture"]
    assert result["sentence"] == (
        "5 registration places available — Limited by lab and lecture."
    )
    assert result["breakdown"] == "Lab 1/1 open, Lecture 1/1 open."


def test_over_capacity_contributes_zero_without_rewriting_totals():
    result = calculate_availability({"L": section("Lecture", 32, 30)})

    assert result["available"] == 0
    assert result["sentence"] == "0 seats open — 32/30 enrolled."
