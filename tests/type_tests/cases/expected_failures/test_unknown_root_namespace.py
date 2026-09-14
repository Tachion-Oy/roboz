import roboz as rz


# Expected: reportAttributeAccessIssue; misspelled namespaces are not accepted.
rz.modles.Str(value="typo")
