import base64
from github import Github
from pprint import pprint
username="finnveloz"
#creates github object
g=Github()
# uses get user method to assign the user with username finnveloz to user
user=g.get_user('finnveloz')
# a loop which prints every repo out of the users owned repos
for repo in user.get_repos():
    print(repo)

        
