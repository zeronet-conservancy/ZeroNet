"""Exceptions that can happen due to incorrect API calls

While it might be counter-intuitive to implement these as exceptions (because
reacting to incorrect outside data is normal), this has been deemed as more
developer friendly solution (since we don't have Either monad in python)
"""

class BadAPICall(RuntimeError):
    """Base exception for API call errors"""
    def __init__(self, message):
        super().__init__(f"Bad API call: {message}")

class BadAddress(BadAPICall):
    """Exception for bad address response"""
    def __init__(self, address):
        super().__init__(f"Bad address: `{address}`")
        self.address = address
