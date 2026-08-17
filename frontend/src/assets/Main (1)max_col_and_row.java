import java.util.*;

class Main{
    public static void main(String[] args){
        Scanner sc = new Scanner(System.in);
        int n=sc.nextInt();
        int[][] arr = new int[n][n];
        for(int i=0;i<n;i++){
            for(int j=0;j<n;j++){
                arr[i][j]=sc.nextInt();
            }
        }
        int maxsum=0;
        int row=0;
        for(int i=0;i<n;i++){
            int sum = 0;
            for(int j=0;j<n;j++){
                sum += arr[i][j];
            }
            if(sum > maxsum){
                maxsum = sum;
                row=i;
            }
        }
        System.out.println("The maxsum of row is: "+maxsum);
        System.out.println("The row is : "+row);
        int column = 0;
        for(int j=0;j<n;j++){
            int sum = 0;
            for(int i=0;i<n;i++){
                sum += arr[i][j];
            }
            if(sum > maxsum){
                maxsum=sum;
                column = j;
            }
        }
        System.out.println("The maxsum of column is: "+maxsum);
        System.out.println("The column is : "+row);
        
    }
}