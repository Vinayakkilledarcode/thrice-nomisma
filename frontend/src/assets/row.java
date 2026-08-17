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
        
        int column = 0;
        int maxsum = Integer.MIN_VALUE;
        for(int j=0;j<n;j++){
            int sum=0;
            for(int i=0;i<n;i++){
                sum += arr[i][j];
            }
            if(sum > maxsum){
                maxsum = sum;
                column = j;
            }
        }
        System.out.println("Maximum sum of row: "+maxsum);
        System.out.println("Column : "+(column + 1));
    }
}