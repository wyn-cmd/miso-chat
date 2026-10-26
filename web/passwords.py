# Password screen for new passwords: the common ones a stranger tries first, passwords that are the username, and runs
# of one or two characters. Length is checked by the caller against MIN_PASSWORD.

# Lower case. Only entries of at least ten characters are listed, because shorter ones are refused by length already.
COMMON = frozenset({
    "password12", "password123", "password1234", "password!1", "password1!", "p@ssword123", "p@ssw0rd123",
    "passw0rd123", "passwordpassword", "mypassword1", "mypassword123", "letmein1234", "letmein12345",
    "welcome1234", "welcome123", "changeme123", "changeme12", "administrator", "admin123456", "admin1234567",
    "1234567890", "0123456789", "0987654321", "9876543210", "1234512345", "123123123123", "1234567890123",
    "12341234123", "123456789012", "1111111111", "2222222222", "5555555555", "0000000000", "11111111111",
    "00000000000", "qwertyuiop", "qwerty1234", "qwerty12345", "qwertyuiop123", "qwertyuiop1", "1q2w3e4r5t",
    "1q2w3e4r5t6y", "qazwsxedc", "zxcvbnm123", "asdfghjkl1", "iloveyou123", "iloveyou12", "monkey12345",
    "dragon12345", "football123", "baseball123", "superman123", "batman12345", "sunshine123", "princess123",
    "starwars123", "computer123", "internet123", "hello12345", "hello world", "test123456", "testtest12",
    "temp1234567", "default1234", "secret1234", "abc123abc123", "abcdefghij", "abcdefghijk", "abcdefghijkl",
    "1234abcd1234", "trustno1234", "whatever123", "freedom1234", "master12345", "shadow12345", "login12345",
})


# Why a new password is refused, or None when it may be used. The username is the account's name as it was typed.
def refusal(password, username=""):
    lowered = password.lower()
    if username and lowered == username.strip().lower():
        return "The password cannot be the same as the username."
    if lowered in COMMON:
        return "That password is too common. Pick one that is harder to guess."
    # a run of one or two characters, such as aaaaaaaaaa or ababababab, is one guess for a stranger
    if len(set(password)) <= 2:
        return "Use a mix of characters, not one or two repeated."
    return None
