import re
import subprocess
from github import Github
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv
import os





def attempt2():
    orgname = 'pointblue'
    ##prompt user enter pa token
    load_dotenv()

    github_token = os.getenv('GITHUB_TOKEN')

    if not github_token:
        envpath=os.path.exists('env')
        if not (envpath):
            with open('.env', 'w') as fh:
                github_token = input('Please enter your GitHub personal access token so that it can be stored for later use: ')
                fh.write(f'GITHUB_TOKEN={github_token}')
                print('If you are pushing commits to this code, remember to add your .env file to .gitignore')

        else:
            print('Please add your GitHub token to your dotenv file')


    ##authenticate with github token
    g = Github(github_token)
    ##gets current date for utc timezone
    current_date = datetime.now(timezone.utc)
    ##gets organization object from github
    org = g.get_organization(orgname)
    ##iterates through each repo in the org
    for repo in org.get_repos():
        ##counter for outputting how many dates within the last 2 weeks were succsusfully outputted
        datecount = 0
        timelist={}
        ##counter for exception handling, increments every time attempt to retrieve commit and fail
        errorcount = 0
        last_commit_date = {}  # Dictionary to store the latest commit date for each branch
        ## prints repo name
        print(f"Repository: {repo.full_name}")
        ##iterates through each branch of repo
        for branch in repo.get_branches():
            ##repo_name assigned to a variable
            repo_name = repo.full_name
            ##branch name assigned to variable
            branch_name = branch.name
            ##prints out branch name
            #print(f'Branch: {branch_name}')
            try:
                ## Fetch the commits for the branch within the last two weeks
                commits = repo.get_commits(since=current_date - timedelta(weeks=2), sha=branch.commit.sha)
                ## Iterate through the commits
                for commit in commits:

                    commit_date = commit.commit.author.date
                    commit_title = commit.commit.message.split('\n')[0]  # Get the first line of commit message as title
                    ## Check if the commit date is within the last two weeks
                    if commit_date > current_date - timedelta(weeks=2):
                        #appends timelise with tuple of commit date and commit title
                        timelist.setdefault(branch_name, []).append((commit_date,commit_title))
                        datecount += 1
                    # Update the last commit date for the branch
                    # ensures each branch has its latest own commit saved
                    if branch_name not in last_commit_date or commit_date > last_commit_date[branch_name]:
                        last_commit_date[branch_name] = commit_date
                # handles exceptions when getting commits
            except Exception as e:
                #prints error message
                print(f'Error fetching commits: {e}')
                errorcount += 1

        # Check if there are branches with no commits in the last two weeks and update last_commit_date
        for branch in repo.get_branches():
            branch_name = branch.name
            if branch_name not in last_commit_date:
                try:
                    #Gets last commit date for branch
                    last_commit_date[branch_name] = branch.commit.commit.author.date
                except Exception as e:
                    print(f'Error fetching last commit date for branch {branch_name}: {e}')
                    errorcount += 1

        # Print the last commit date for each branch
        for branch_name, branch_last_commit_date in last_commit_date.items():
            print(f"Branch: {branch_name}, Last Commit Date: {branch_last_commit_date}")

        # Print the repo name and its last commit dates
        #print(f"Repo: {repo_name}, Last Commit Dates: {last_commit_date}")

        print(f'{datecount} commits within 2 weeks successfully retrieved, {errorcount} errors in retrieval')
        print(f'Commits made in the last 2 weeks: ')
        for branch_name, commits in timelist.items():
            #formatted_commits = ", ".join(commit_date.strftime('%Y-%m-%d %H:%M:%S %Z') for commit_date in commits)
            print(f'Branch: {branch_name}')
            ##for every commit date and title in commits, format the date into an easily readable format, and print the title
            for commit_date,commit_title in commits:
                formatted_date = commit_date.strftime('%Y-%m-%d %H:%M:%S %Z')
                print(f"  - Date: {formatted_date}, Title: {commit_title}")


        print('=' * 50)

attempt2()