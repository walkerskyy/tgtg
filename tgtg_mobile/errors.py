class Error(Exception):
    pass


class TgtgLoginError(Error):
    pass


class TgtgAPIError(Error):
    pass


class TgtgAuthError(TgtgAPIError):
    pass


class TgtgCaptchaError(TgtgAPIError):
    pass


class TgtgPollingError(TgtgAPIError):
    pass


class ConfigurationError(Error):
    pass


class MaskConfigurationError(ConfigurationError):
    def __init__(self, variable):
        self.message = f"Unrecognized variable {variable}..."
        super().__init__(self.message)


class TgtgConfigurationError(ConfigurationError):
    def __init__(self, message="Invalid TGTG configuration"):
        self.message = message
        super().__init__(self.message)


class TokenStorageError(Error):
    pass
