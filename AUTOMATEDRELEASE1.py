import requests
##imports pretty print
from pprint import pprint
#githubusername
username='finnveloz'
url=f'https://api.github.com/users/{username}'
#make the request and return json
user_data=requests.get(url).json()
pprint(user_data)
