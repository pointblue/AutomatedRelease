import re
import subprocess
from github import Github
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv
import os


def get_sprintdates():
    today = datetime.now(timezone.utc)
    ##test cases
    #today=datetime(2024,2,7,tzinfo=timezone.utc) #(date in even week that shouldnt be output) outputs 2024-01-15 tom 2024-01-26
    #today=datetime(2024,1,30,tzinfo=timezone.utc) # start in odd week OUTPUTS 2024-01-15 to 2024-01-26
    #today = datetime(2024, 1, 29, tzinfo=timezone.utc) #2024-01-15 to 2024-01-26
    #today = datetime(2024, 1, 29, tzinfo=timezone.utc) #2024-01-15 to 2024-01-26
    #today = datetime(2024, 1, 28, tzinfo=timezone.utc) #2024-01-01 to 2024-01-12 NOW 2024-01-15 to 2024-01-26 saturday case
    #today = datetime(2024, 1, 26, tzinfo=timezone.utc) #2024-01-01 to 2024-01-12 correct
    #today = datetime(2024, 1, 21, tzinfo=timezone.utc) # 2024-02-12 to 2024-02-23
    #today = datetime(2024, 2, 10, tzinfo=timezone.utc) # 2024-01-29 to 2024-02-09
    #today = datetime(2024, 2, 17, tzinfo=timezone.utc) # 2024-01-29 to 2024-02-09




    ##
    first_day_of_year = datetime(today.year, 1, 1, tzinfo=timezone.utc)
    weeks_since_start_of_year = (today - first_day_of_year).days // 7

    sprint_start_date = first_day_of_year + timedelta(weeks=weeks_since_start_of_year)
    sprint_end_date = sprint_start_date + timedelta(days=4) + timedelta(weeks=1)


    if sprint_end_date > today:
        #moves back if sprint end is in future
        sprint_start_date -= timedelta(weeks=2)
        sprint_end_date -= timedelta(weeks=1)
    ############
    if sprint_start_date.weekday() != 0 or weeks_since_start_of_year % 2 == 0:
        sprint_start_date += timedelta(weeks=1)

        # Adjust the end date to the Friday of the even week
    if sprint_end_date.weekday() != 4 or weeks_since_start_of_year % 2 != 0:
        sprint_end_date += timedelta(weeks=1)
    ###########
    # Move to the Monday that started the sprint
    sprint_start_date += timedelta(days=(7 - sprint_start_date.weekday()))
    #print(f'{sprint_start_date, sprint_end_date}')
    ##

    ##CHECKS IF TODAY IS SATURDAY OR SUNDAY
    if(today.weekday()==5 or today.weekday()==6):
        sprint_end_date = sprint_start_date + timedelta(days=4) + timedelta(weeks=1)
        #print(f'{sprint_start_date, sprint_end_date},NOOO')

        ##MAKES SURE END DATE NOT IN FUTURE AGAIN
        if not(sprint_end_date>today):
            #print(f'{sprint_start_date, sprint_end_date},YESSSS')
            return sprint_start_date,sprint_end_date
    ##
    while(sprint_end_date>=today):
        sprint_start_date -= timedelta(weeks=2)
        sprint_end_date -= timedelta(weeks=2)
        #print(f'{sprint_start_date, sprint_end_date}')
    sprint_end_date = sprint_start_date + timedelta(days=4) + timedelta(weeks=1)
    return sprint_start_date, sprint_end_date

def fetch_commits_within_sprint(repo, sprint_start_date, sprint_end_date):
    sprint_commits = []
    errorlist=[]
    errordict={}

    try:
        dev_branch = repo.get_branch("dev")
        #print(f"Fetching commits for {repo.full_name}...")
        commits = repo.get_commits(since=sprint_start_date, until=sprint_end_date, sha=dev_branch.commit.sha)
        for commit in commits:
            commit_date = commit.commit.author.date
            sprint_commits.append(('dev', commit_date, commit.commit.message.split('\n')[0]))


    except Exception as e:
        ##outputs list of repos where no commits found, no dev branch
        errorlist.append((repo.full_name,e))




    return sprint_commits,errorlist



def print_commits():


    orgname = 'pointblue'
    errorlist=[]
    load_dotenv()

    github_token = os.getenv('GITHUB_TOKEN')
    ###if the program can't find github token, it checks if path to dotenv file exists, if not, then it creates one and configures it


    sprint_start_date, sprint_end_date = get_sprintdates()

    ###THESE 2 lines  CAN BE USED TO TEST THIS PROGRAM, just change date to date range you want to test
    #sprint_start_date = datetime(datetime.now().year, 3, 1, tzinfo=timezone.utc)
    #sprint_end_date = datetime(datetime.now().year, 2, 23, tzinfo=timezone.utc)


    print(f'Current sprint: {sprint_start_date.strftime("%Y-%m-%d")} to {sprint_end_date.strftime("%Y-%m-%d")}')
    if not github_token:
        envpath = os.path.exists('env')
        print('No Github Token')
        if not (envpath):
            with open('.env', 'w') as fh:
                github_token = input(
                    'Please enter your GitHub personal access token so that it can be stored for later use: ')
                fh.write(f'GITHUB_TOKEN={github_token}')
                print('If you are pushing commits to this code, remember to add your .env file to .gitignore')

        else:
            print('Please add your GitHub token to your dotenv file')

    g = Github(github_token)
    org = g.get_organization(orgname)

    for repo in org.get_repos():
        try:
            ##stores the date, title,and branch in commits in branch dev from sprint period
            out,errors = fetch_commits_within_sprint(repo, sprint_start_date, sprint_end_date)
            ##appends errorlist with error messages that are not empty
            if(errors):
                errorlist.extend(errors)

            if (out):
                print(f"Repository: {repo.full_name}")
                for branch_name, commit_date, commit_title in out:
                    formatted_date = commit_date.strftime('%Y-%m-%d %H:%M:%S %Z')
                    print(f'Branch: {branch_name}, Date: {formatted_date}, Title: {commit_title}')
                print('=' * 50)

        except Exception as e:
            print(f'Error fetching  for repo  {repo.full_name} : {e}')
    for repo,error in errorlist:
        if isinstance(error, Exception):
            if hasattr(error, 'data') and 'message' in error.data:
                error_message = error.data['message']
                if not "Branch not found" in error_message:
                    print(f'Error in repository {repo}: {error}')
        else:

            pass




        # Check if there are branches with no commits in the last two weeks and update last_commit_date
print_commits()
print('END')

