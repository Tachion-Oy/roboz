import roboz as rz


# Expected: reportAttributeAccessIssue; models are owned by roboz.models.
rz.Str(value="use rz.models.Str")
