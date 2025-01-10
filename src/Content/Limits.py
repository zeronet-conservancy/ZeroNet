"""New-style content storage limits.

WIP
"""

from rich import print
from .ContentDb import getContentDb

def applyLimitRules(rules):
    print("Rules before limits")
    if not rules:
        return rules
    print(rules)
    if 'user_address' not in rules:
        # TODO
        return rules
    cdb = getContentDb()
    limits = cdb.getSizeLimitRulesFor(rules['user_address'])
    print(limits)
    max_size = rules['max_size']
    for limit in limits:
        if limit['rule'] == 'allow':
            max_size = max(limit['value'], max_size)
        elif limit['rule'] == 'limit':
            max_size = min(limit['value'], max_size)
    print("After limits")
    rules['max_size'] = max_size
    print(rules)
    return rules

def canPost(user_address):
    # TODO
    return True

def getSiteLimit(address):
    res = db.execute(
        'SELECT * FROM size_limit WHERE site_id IN (SELECT site_id FROM site WHERE ?',
        {'address': address}
    )

def updateLimitData(site, content):
    address = site.address
    priority = site.settings.get('use_limit_priority')
    if priority is None:
        return
    if 'user_contents' not in content:
        return
    permission_rules = content['user_contents'].get('permissions')
    if permission_rules is None:
        return
    cdb = getContentDb()
    # TODO: make updating more effecient maybe?
    cdb.execute('DELETE FROM size_limit WHERE source = ?', (address,))
    for target, rules in permission_rules.items():
        limit_rule = convertRules(rules)
        print(limit_rule)
        cdb.execute('INSERT INTO size_limit ?', {
            'address': target,
            'source': address,
            'is_private': True,
            'priority': priority,
            **limit_rule
        })

def convertRules(rules):
    if not rules or 'max_size' not in rules:
        return {
            'rule': 'limit',
            'value': 0,
        }
    return {
        'rule': 'allow',
        'value': rules['max_size'],
    }
