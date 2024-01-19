import base64
from github import Github
from pprint import pprint
username="finnveloz"
g=Github()
user=g.get_user('finnveloz')
for repo in user.get_repos():
    print(repo)

        
